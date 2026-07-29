"""Probe A — answer-token log-odds shift (teacher vs student prompt).

Non-reasoning closer ("Your answer:"): for both action labels, teacher-force
the label continuation and sum its token logprobs under the plain (student)
and moral-value (teacher) prompt. Deterministic — two forward passes per
state, no sampling noise. Per fabricated state (first/CC/CD/DC/DD):
    logodds = logP(coop) - logP(defect)          (per prompt)
    delta   = logodds_teacher - logodds_student
    jsd     = Jensen-Shannon divergence of the two-way {C, D} distributions
              (the divergence family the SDPO loss uses).
Reciprocity in logit space = state-dependent sign flip of delta: toward C
in opp-C states, NOT toward C in opp-D states.

Writes logprob_a.json into --output-dir.

Usage (inside the moralgym_verl container, 1 GPU):
    python3 -m moralgym_verl.eval.probe_answer_token \
        --config configs/eval/teacher_signal_9b.yaml \
        --moral-value deontological --game prisoners_dilemma \
        --output-dir <run_dir>
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Dict

import torch

from moralgym_verl.eval.behavioral import (
    build_eval_config, load_config, load_model_for_eval,
)
from moralgym_verl.eval.teacher_context import load_reprompt_template, wrap_prompt
from moralgym_verl.eval.teacher_forcing import (
    PROBE_STATES, answer_logodds, chat_prefix, force_fixed_presentation,
    two_way_jsd,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS
from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.game.prompts import build_prompt

logger = logging.getLogger(__name__)


def answer_token_probe(model, tokenizer, config, wrapper) -> Dict:
    """Label logodds shift + JSD per state, non-reasoning closer."""
    cfg = copy.deepcopy(config)
    cfg.reasoning = False
    results = {}
    for state, hist_a, hist_o in PROBE_STATES:
        game_prompt = build_prompt(cfg, hist_a, hist_o)
        student_ids = chat_prefix(tokenizer, game_prompt, model.device)
        teacher_ids = chat_prefix(tokenizer, wrapper(game_prompt), model.device)
        lo_s, lp_cs, lp_ds = answer_logodds(
            model, tokenizer, student_ids, cfg.coop_label, cfg.defect_label)
        lo_t, lp_ct, lp_dt = answer_logodds(
            model, tokenizer, teacher_ids, cfg.coop_label, cfg.defect_label)
        results[state] = {
            "logodds_student": lo_s,
            "logodds_teacher": lo_t,
            "delta": lo_t - lo_s,
            "p_coop_student": 1 / (1 + math.exp(-lo_s)),
            "p_coop_teacher": 1 / (1 + math.exp(-lo_t)),
            "jsd": two_way_jsd(lp_cs, lp_ds, lp_ct, lp_dt),
        }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe A: answer-token log-odds shift")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", default="base",
                        help="'base' or LoRA checkpoint path (as behavioral.py)")
    parser.add_argument("--moral-value", required=True,
                        help="Wording (or '+'-composite) to probe; not 'none' "
                             "(the probe compares against the plain prompt "
                             "internally).")
    parser.add_argument("--game", default=None, choices=sorted(FIXED_PAYOFFS))
    parser.add_argument("--output-dir", default="results",
                        help="Run directory; writes logprob_a.json into it.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

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

    # Opponent is irrelevant here — the probe conditions on fabricated states.
    force_fixed_presentation(cfg)
    config = build_eval_config(cfg, opponent="tit_for_tat")

    logger.info("Probe A (answer-token) over %d states ...", len(PROBE_STATES))
    probe_a = answer_token_probe(model, tokenizer, config, wrapper)
    for state, r in probe_a.items():
        logger.info("  %s: p(C) %.2f -> %.2f  (delta logodds %+.2f, jsd %.4f)",
                    state, r["p_coop_student"], r["p_coop_teacher"],
                    r["delta"], r["jsd"])

    metadata = {
        "probe": "logprob_a_answer_token",
        "moral_value": args.moral_value,
        "base_model": cfg["policy"]["model_name"],
        "checkpoint": args.checkpoint,
        "game_type": cfg["game"]["type"],
        "teacher_template_source": teacher_cfg.get("template_source"),
        "eval_seed": seed,
        "timestamp": datetime.now().isoformat(),
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path_a = out_dir / "logprob_a.json"
    with open(path_a, "w") as f:
        json.dump({"metadata": metadata, "answer_token_probe": probe_a},
                  f, indent=2)
    logger.info("Probe A results saved to %s", path_a)


if __name__ == "__main__":
    main()
