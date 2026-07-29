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
from pathlib import Path
from typing import Dict

from moralgym_verl.eval.teacher_forcing import (
    PROBE_STATES, answer_logodds, chat_prefix, probe_setup, two_way_jsd,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS
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
                        help="'base', LoRA adapter, or full-model checkpoint "
                             "path (as behavioral.py)")
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

    # Opponent is irrelevant here — the probe conditions on fabricated states.
    _, config, model, tokenizer, wrapper, metadata = probe_setup(args)

    logger.info("Probe A (answer-token) over %d states ...", len(PROBE_STATES))
    probe_a = answer_token_probe(model, tokenizer, config, wrapper)
    for state, r in probe_a.items():
        logger.info("  %s: p(C) %.2f -> %.2f  (delta logodds %+.2f, jsd %.4f)",
                    state, r["p_coop_student"], r["p_coop_teacher"],
                    r["delta"], r["jsd"])

    metadata = {**metadata, "probe": "logprob_a_answer_token"}
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path_a = out_dir / "logprob_a.json"
    with open(path_a, "w") as f:
        json.dump({"metadata": metadata, "answer_token_probe": probe_a},
                  f, indent=2)
    logger.info("Probe A results saved to %s", path_a)


if __name__ == "__main__":
    main()
