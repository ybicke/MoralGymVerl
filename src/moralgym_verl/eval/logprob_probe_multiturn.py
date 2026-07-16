"""Multi-turn teacher-signal probe: per-round SDPO signal decay.

In multi-turn SDPO the moral value sits ONLY in the episode's first user
message (the trainer wraps raw_prompt; all later rounds are shared
response-region tokens — see docs/teacher_signal_eval.md). This probe
measures whether that episode-start signal still moves token
probabilities in later rounds, i.e. whether stock multi-turn SDPO can
push round-5 decisions at all.

Procedure (mirrors training exactly):
  1. Generate K episodes from the STUDENT context (plain prompts,
     transcript accumulates like the verl agent loop; opponent reacts).
  2. Per round r, score that round's trace twice with growing prefixes:
       student prefix: chat([u1, a1, ..., u_{r+1}])
       teacher prefix: same, with u1 wrapped (wrap_first_user)
     -> token_delta(r): mean per-token logprob gap on the round's trace;
     -> answer_delta(r): label log-odds shift after the trace's final
        `Action:` marker, conditioned on the same transcript-so-far.
  3. Aggregate mean±std per round index over the K episodes.

Reading: answer_delta flat across rounds -> signal reaches late rounds,
stock multi-turn SDPO is fine. Decaying toward 0 -> late-round decisions
are untrainable by episode-start context; multi-turn context distillation
needs per-round-sample splitting (or train single-turn).

Usage (inside the moralgym_verl container, 1 GPU):
    python3 -m moralgym_verl.eval.logprob_probe_multiturn \
        --config configs/eval/teacher_signal_9b.yaml \
        --moral-value deontological --output-dir <run_dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import torch

from moralgym_verl.eval.behavioral import build_eval_config, load_config, load_model_for_eval
from moralgym_verl.eval.logprob_probe import (
    _answer_logodds, _chat_prefix, _continuation_logprob, _sample_trace,
)
from moralgym_verl.eval.teacher_context import (
    load_reprompt_template, wrap_first_user, wrap_prompt,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS
from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.game.players import get_opponent_action
from moralgym_verl.game.prompts import build_prompt, parse_action

logger = logging.getLogger(__name__)

_ACTION_MARK = re.compile(r"[Aa]ction\s*[:\-]")


def _chat_prefix_messages(tokenizer, messages: List[dict], device):
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    return tokenizer(text, return_tensors="pt").input_ids.to(device)


def run_probe_episode(
    model, tokenizer, config, wrapper,
    max_new_tokens: int, temperature: float,
) -> List[Dict]:
    """One student episode + per-round dual scoring. Returns one record
    per round: {round, trace, token_delta, answer_delta, agent, opp}."""
    messages: List[dict] = []
    agent_history: List[str] = []
    opp_history: List[str] = []
    records: List[Dict] = []

    for rnd in range(config.num_rounds):
        prompt = build_prompt(config, agent_history, opp_history)
        messages.append({"role": "user", "content": prompt})

        # Student generates (plain context — as in training rollouts).
        student_ids = _chat_prefix_messages(tokenizer, messages, model.device)
        trace = _sample_trace(model, tokenizer, student_ids,
                              max_new_tokens, temperature)
        messages.append({"role": "assistant", "content": trace})

        # Teacher prefix: identical transcript, first user turn wrapped.
        teacher_ids = _chat_prefix_messages(
            tokenizer, wrap_first_user(messages[:-1], wrapper), model.device)

        record: Dict = {"round": rnd + 1, "trace": trace,
                        "token_delta": None, "answer_delta": None}
        lp_s, n_s = _continuation_logprob(model, tokenizer, student_ids, trace)
        lp_t, n_t = _continuation_logprob(model, tokenizer, teacher_ids, trace)
        if n_s:
            record["token_delta"] = lp_t / n_t - lp_s / n_s

        marks = list(_ACTION_MARK.finditer(trace))
        if marks:
            reasoning_prefix = trace[: marks[-1].end()]
            cut_ids = tokenizer(reasoning_prefix, add_special_tokens=False,
                                return_tensors="pt").input_ids.to(model.device)
            lo_s, _, _ = _answer_logodds(
                model, tokenizer, torch.cat([student_ids, cut_ids], dim=1),
                config.coop_label, config.defect_label)
            lo_t, _, _ = _answer_logodds(
                model, tokenizer, torch.cat([teacher_ids, cut_ids], dim=1),
                config.coop_label, config.defect_label)
            record["answer_delta"] = lo_t - lo_s

        # Advance the game (mirrors trajectory.run_episode semantics).
        action = parse_action(trace, config)
        if action is None:
            record["agent"], record["opp"] = "illegal", None
        else:
            opp_action = get_opponent_action(
                config.opponent, opp_history, agent_history)
            agent_history.append(action)
            opp_history.append(opp_action)
            record["agent"], record["opp"] = action, opp_action
        records.append(record)
    return records


def _stats(xs: List[float]) -> Dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"mean": None, "std": None, "n": 0}
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return {"mean": mean, "std": math.sqrt(var), "n": len(xs)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-turn teacher-signal probe")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", default="base")
    parser.add_argument("--moral-value", required=True)
    parser.add_argument("--game", default=None, choices=sorted(FIXED_PAYOFFS))
    parser.add_argument("--episodes", type=int, default=8,
                        help="Student episodes to generate and score")
    parser.add_argument("--opponent", default="tit_for_tat",
                        help="Opponent for the probe episodes (training default)")
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])

    moral_text = get_moral_value(args.moral_value)
    if not moral_text:
        raise SystemExit("--moral-value 'none' is meaningless here.")
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

    config = build_eval_config(cfg, opponent=args.opponent)
    eval_cfg = cfg.get("evaluation", {})
    max_new_tokens = eval_cfg.get("max_new_tokens", 512)
    temperature = eval_cfg.get("temperature", 0.7)

    logger.info("Multi-turn probe: %d episodes x %d rounds vs %s ...",
                args.episodes, config.num_rounds, args.opponent)
    all_records: List[Dict] = []
    for ep in range(args.episodes):
        records = run_probe_episode(model, tokenizer, config, wrapper,
                                    max_new_tokens, temperature)
        for r in records:
            r["episode"] = ep
        all_records.extend(records)

    per_round = {}
    for rnd in range(1, config.num_rounds + 1):
        rows = [r for r in all_records if r["round"] == rnd]
        per_round[f"round_{rnd}"] = {
            "token_delta": _stats([r["token_delta"] for r in rows]),
            "answer_delta": _stats([r["answer_delta"] for r in rows]),
        }
        td, ad = per_round[f"round_{rnd}"]["token_delta"], per_round[f"round_{rnd}"]["answer_delta"]
        logger.info("  round %d: token_delta %s  answer_delta %s",
                    rnd, td["mean"], ad["mean"])

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "logprob_multiturn.json", "w") as f:
        json.dump({
            "metadata": {
                "probe": "logprob_multiturn_decay",
                "moral_value": args.moral_value,
                "base_model": cfg["policy"]["model_name"],
                "checkpoint": args.checkpoint,
                "game_type": cfg["game"]["type"],
                "opponent": args.opponent,
                "episodes": args.episodes,
                "num_rounds": config.num_rounds,
                "wrap_position": "first",
                "temperature": temperature,
                "seed": seed,
                "timestamp": datetime.now().isoformat(),
            },
            "per_round": per_round,
        }, f, indent=2)
    with open(out_dir / "logprob_multiturn.traces.jsonl", "w") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("Saved to %s", out_dir)


if __name__ == "__main__":
    main()
