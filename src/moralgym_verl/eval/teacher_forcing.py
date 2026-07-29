"""Teacher-forcing measurement primitives shared by the logprob probes.

Machinery, not an experiment: chat-prefix construction, teacher-forced
continuation logprobs, answer-token log-odds, two-way JSD, trace sampling
and delta statistics — plus `probe_setup`, the shared CLI/config/model
setup that keeps the probe entry points in lockstep. The experiments that
use these live in probe_answer_token.py (probe A) and
probe_reasoning_trace.py (probe B).
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import torch

from moralgym_verl.eval.behavioral import (
    build_eval_config, load_config, load_model_for_eval, render_chat_inputs,
)
from moralgym_verl.eval.teacher_context import load_reprompt_template, wrap_prompt
from moralgym_verl.game.environment import FIXED_PAYOFFS
from moralgym_verl.game.moral_values import get_moral_value

logger = logging.getLogger(__name__)

# (state_name, agent_history, opp_history) — one fabricated prior round.
PROBE_STATES: List[Tuple[str, List[str], List[str]]] = [
    ("first", [], []),
    ("CC", ["C"], ["C"]),   # I cooperated, opp cooperated
    ("CD", ["C"], ["D"]),   # I cooperated, opp defected (sucker)
    ("DC", ["D"], ["C"]),   # I defected, opp cooperated (exploit)
    ("DD", ["D"], ["D"]),   # mutual defection
]

_PRESENTATION_AXES = ("tokens", "layout", "prose", "role", "payoffs")


def force_fixed_presentation(cfg: Dict) -> List[str]:
    """Force all presentation axes to 'fixed' in `cfg` (in place).

    Probes are fixed-presentation diagnostics: cells must stay comparable
    across values/runs and never draw from an unpaired RNG stream.
    Returns the axes that had to be overridden (logged as a warning).
    """
    randomized = [ax for ax in _PRESENTATION_AXES
                  if cfg.get("evaluation", {}).get(ax, "fixed") != "fixed"]
    if randomized:
        logger.warning("Ignoring randomized presentation axes %s — probes "
                       "always run fixed presentation.", randomized)
        for ax in randomized:
            cfg["evaluation"][ax] = "fixed"
    return randomized


def chat_prefix(tokenizer, user_text: str, device) -> torch.Tensor:
    """Chat-templated single-user-turn prompt ids (shared single-BOS path)."""
    _, inputs = render_chat_inputs(
        tokenizer, [{"role": "user", "content": user_text}], device,
    )
    return inputs.input_ids


def chat_prefix_messages(tokenizer, messages: List[dict], device) -> torch.Tensor:
    """Chat-templated transcript ids (shared single-BOS path)."""
    _, inputs = render_chat_inputs(tokenizer, messages, device)
    return inputs.input_ids


@torch.no_grad()
def continuation_logprob(
    model, tokenizer, prefix_ids: torch.Tensor, continuation: str
) -> Tuple[float, int]:
    """Sum of token logprobs of `continuation` teacher-forced after prefix.

    Note: the continuation is tokenized independently and concatenated to
    prefix_ids, which can differ from how the joined text would tokenize at
    the seam (SentencePiece boundary merges). Teacher and student passes use
    the identical construction, so reported deltas are internally consistent;
    absolute logprobs are not exactly "the model's natural tokenization".
    """
    cont_ids = tokenizer(
        continuation, add_special_tokens=False, return_tensors="pt"
    ).input_ids.to(prefix_ids.device)
    input_ids = torch.cat([prefix_ids, cont_ids], dim=1)
    logits = model(input_ids).logits
    logprobs = torch.log_softmax(logits[:, :-1].float(), dim=-1)
    targets = input_ids[:, 1:]
    token_lp = logprobs.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
    cont_lp = token_lp[:, prefix_ids.shape[1] - 1:]
    return cont_lp.sum().item(), cont_lp.shape[1]


def two_way_jsd(lp_c1: float, lp_d1: float, lp_c2: float, lp_d2: float) -> float:
    """JSD between the two-way {C, D} distributions implied by sequence
    logprobs (renormalized over the label pair, natural log)."""
    def norm(lp_c, lp_d):
        m = max(lp_c, lp_d)
        pc, pd = math.exp(lp_c - m), math.exp(lp_d - m)
        z = pc + pd
        return pc / z, pd / z

    p, q = norm(lp_c1, lp_d1), norm(lp_c2, lp_d2)
    mix = [(pi + qi) / 2 for pi, qi in zip(p, q)]

    def kl(a, b):
        return sum(ai * math.log(ai / bi) for ai, bi in zip(a, b) if ai > 0)

    return 0.5 * kl(p, mix) + 0.5 * kl(q, mix)


def answer_logodds(
    model, tokenizer, prefix_ids: torch.Tensor, coop_label: str, defect_label: str
) -> Tuple[float, float, float]:
    """(logodds, lp_coop, lp_defect) for ' <label>' continuations."""
    lp_c, _ = continuation_logprob(model, tokenizer, prefix_ids, f" {coop_label}")
    lp_d, _ = continuation_logprob(model, tokenizer, prefix_ids, f" {defect_label}")
    return lp_c - lp_d, lp_c, lp_d


@torch.no_grad()
def sample_trace(model, tokenizer, prefix_ids, max_new_tokens, temperature) -> str:
    out = model.generate(
        input_ids=prefix_ids,
        max_new_tokens=max_new_tokens,
        do_sample=temperature > 0,
        temperature=temperature if temperature > 0 else None,
        top_k=0, top_p=1.0,
    )
    return tokenizer.decode(out[0][prefix_ids.shape[1]:], skip_special_tokens=True)


def delta_stats(xs: List[Optional[float]]) -> Dict:
    """{mean, std, n} over the non-None entries (population std)."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"mean": None, "std": None, "n": 0}
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return {"mean": mean, "std": math.sqrt(var), "n": len(xs)}


def probe_setup(args, opponent: str = "tit_for_tat"):
    """Shared CLI setup for the probe entry points.

    Handles: config load + --game override, moral-value guard and teacher
    wrapper (template from the training yaml), seeding, model load, fixed
    presentation, EpisodeConfig. Keeps the two probe main()s in lockstep.

    Returns (cfg, config, model, tokenizer, wrapper, metadata) — metadata
    carries the provenance fields shared by all probe outputs.
    """
    cfg = load_config(args.config)
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])

    moral_text = get_moral_value(args.moral_value)
    if not moral_text:
        raise SystemExit("--moral-value 'none' is meaningless here: the probe "
                         "always compares against the plain prompt.")

    teacher_cfg = cfg.get("teacher") or {}
    template = load_reprompt_template(teacher_cfg["template_source"])
    feedback_template = teacher_cfg.get("feedback_template")

    def wrapper(prompt: str) -> str:
        return wrap_prompt(prompt, template, moral_text, feedback_template)

    seed = cfg.get("seed", 42)
    random.seed(seed)
    torch.manual_seed(seed)

    checkpoint = None if args.checkpoint == "base" else args.checkpoint
    model, tokenizer = load_model_for_eval(checkpoint, cfg["policy"]["model_name"])

    force_fixed_presentation(cfg)
    config = build_eval_config(cfg, opponent=opponent)

    metadata = {
        "moral_value": args.moral_value,
        "base_model": cfg["policy"]["model_name"],
        "checkpoint": args.checkpoint,
        "game_type": cfg["game"]["type"],
        "teacher_template_source": teacher_cfg.get("template_source"),
        "eval_seed": seed,
        "timestamp": datetime.now().isoformat(),
    }
    return cfg, config, model, tokenizer, wrapper, metadata
