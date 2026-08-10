"""Teacher-forcing measurement primitives shared by the logprob probes.

Machinery, not an experiment: chat prefixes, teacher-forced logprobs,
answer log-odds, JSDs, trace sampling, delta stats, and `probe_setup`
(shared CLI/config/model setup keeping the probe entry points in
lockstep). The experiments live in probe_a.py (A) and
probe_b.py (B).
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import torch

from moralgym_verl.eval.config import (
    apply_protocol, build_eval_config, git_provenance, load_config,
)
from moralgym_verl.eval.generation import render_chat_inputs
from moralgym_verl.eval.model_loading import load_model_for_eval
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

_PRESENTATION_AXES = ("labels", "layout", "label_order", "role", "payoffs")


def force_fixed_presentation(cfg: Dict) -> List[str]:
    """Force all presentation axes to 'fixed' in `cfg` (in place); return
    the overridden axes (logged as a warning). Probes are fixed-
    presentation diagnostics: cells must stay comparable across values/
    runs and never draw from an unpaired RNG stream."""
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

    Seam caveat: the continuation is tokenized independently and
    concatenated, which can differ from how the joined text would tokenize
    (SentencePiece boundary merges). Teacher and student use the identical
    construction, so deltas are consistent; absolute logprobs are not
    "the model's natural tokenization".
    """
    cont_ids = tokenizer(
        continuation, add_special_tokens=False, return_tensors="pt"
    ).input_ids.to(prefix_ids.device)
    input_ids = torch.cat([prefix_ids, cont_ids], dim=1)
    logits = model(input_ids).logits
    # fp32 softmax only over the K continuation positions — full-sequence
    # would materialize a [seq, vocab] fp32 tensor (GBs at Gemma's 256k vocab).
    K = cont_ids.shape[1]
    start = prefix_ids.shape[1] - 1
    logprobs = torch.log_softmax(logits[:, start:start + K].float(), dim=-1)
    token_lp = logprobs.gather(-1, cont_ids.unsqueeze(-1)).squeeze(-1)
    return token_lp.sum().item(), K


def generalized_jsd(
    logp_s: torch.Tensor, logp_t: torch.Tensor, alpha: float
) -> torch.Tensor:
    """Per-position generalized JSD between two [K, V] log-prob tensors;
    returns [K]. Mirrors SDPO's compute_self_distillation_loss
    (core_algos.py) exactly, branches included: alpha=0 -> KL(t||s),
    alpha=1 -> KL(s||t), else (1-a)*KL(s||m) + a*KL(t||m),
    m = (1-a)*s + a*t."""
    def kl(logp, logq):  # KL(p || q), summed over vocab
        return (logp.exp() * (logp - logq)).sum(-1)

    if alpha == 0.0:
        return kl(logp_t, logp_s)
    if alpha == 1.0:
        return kl(logp_s, logp_t)
    log_m = torch.logsumexp(
        torch.stack([logp_s + math.log(1 - alpha), logp_t + math.log(alpha)]),
        dim=0,
    )
    return (1 - alpha) * kl(logp_s, log_m) + alpha * kl(logp_t, log_m)


@torch.no_grad()
def dual_continuation_scores(
    model, tokenizer,
    student_prefix: torch.Tensor, teacher_prefix: torch.Tensor,
    continuation: str, alpha: float,
) -> Dict:
    """Teacher-force `continuation` after both prefixes: per-position
    actual-token logprobs under each, plus the full-vocab generalized JSD
    between the two next-token distributions (the step-0 SDPO per-token
    loss). Returns {lp_s, lp_t, num_tokens, token_jsd} (lp_* = summed
    sequence logprobs, token_jsd = per-position mean). Same seam caveat
    as continuation_logprob."""
    cont_ids = tokenizer(
        continuation, add_special_tokens=False, return_tensors="pt"
    ).input_ids.to(student_prefix.device)
    K = cont_ids.shape[1]
    if K == 0:
        return {"lp_s": 0.0, "lp_t": 0.0, "num_tokens": 0, "token_jsd": None}

    rows = {}
    for name, prefix in (("s", student_prefix), ("t", teacher_prefix)):
        input_ids = torch.cat([prefix, cont_ids], dim=1)
        logits = model(input_ids).logits
        # Positions predicting the K continuation tokens.
        start = prefix.shape[1] - 1
        rows[name] = torch.log_softmax(
            logits[0, start:start + K].float(), dim=-1
        )
    token_lp_s = rows["s"].gather(-1, cont_ids[0].unsqueeze(-1)).squeeze(-1)
    token_lp_t = rows["t"].gather(-1, cont_ids[0].unsqueeze(-1)).squeeze(-1)
    jsd = generalized_jsd(rows["s"], rows["t"], alpha)
    return {
        "lp_s": token_lp_s.sum().item(),
        "lp_t": token_lp_t.sum().item(),
        "num_tokens": K,
        "token_jsd": jsd.mean().item(),
    }


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
    """Shared CLI setup keeping the two probe main()s in lockstep: config
    + --game override, moral-value guard, teacher wrapper (template from
    the training yaml), seeding, model load, fixed presentation,
    EpisodeConfig. Returns (cfg, config, model, tokenizer, wrapper,
    metadata) — metadata holds the provenance fields shared by all probe
    outputs."""
    cfg = load_config(args.config)
    # Model and turn structure are cell properties, not config properties:
    # a sweep cell's probes must describe the same weights and the same
    # phase as its behavioral run. Without these two lines the probes fall
    # back to whatever the eval yaml declares (a 5-round multi-round config),
    # so a single-turn cell would carry probes built for a different phase.
    if getattr(args, "model", None):
        cfg["policy"]["model_name"] = args.model
    if getattr(args, "protocol", None):
        apply_protocol(cfg, args.protocol)
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])
    # Representation is a prompt-block variant, not one of the five
    # force_fixed_presentation axes: probes on a prose/list cell must
    # render that cell's payoff block, so it passes through untouched.
    if getattr(args, "representation", None):
        cfg.setdefault("prompt", {})["representation"] = args.representation

    moral_text = get_moral_value(args.moral_value)
    if not moral_text:
        raise SystemExit("--moral-value 'none' is meaningless here: the probe "
                         "always compares against the plain prompt.")

    teacher_cfg = cfg.get("teacher") or {}
    template_source = teacher_cfg.get("template_source")
    if not template_source:
        raise ValueError(
            "config has no teacher.template_source (path to the SDPO training "
            "yaml the reprompt_template is read from) — required by the probes"
        )
    template = load_reprompt_template(template_source)
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
        # Phase provenance, mirroring behavioral.build_metadata: which preset
        # the cell declared and the turn structure it resolved to.
        "protocol": getattr(args, "protocol", None) or "custom",
        "num_rounds": cfg["game"]["num_rounds"],
        "game_type": cfg["game"]["type"],
        "representation": cfg.get("prompt", {}).get("representation", "matrix"),
        "teacher_template_source": teacher_cfg.get("template_source"),
        "eval_seed": seed,
        "timestamp": datetime.now().isoformat(),
        "git_commit": git_provenance(),
        "config": args.config,
    }
    return cfg, config, model, tokenizer, wrapper, metadata
