"""Probe A — answer-token log-odds shift (teacher vs student prompt).

Non-reasoning closer: teacher-force both action labels and sum token
logprobs under the plain (student) and moral-value (teacher) prompt —
deterministic, two forward passes per fabricated state (first/CC/CD/DC/DD).
Per state: logodds = logP(coop) - logP(defect); delta = teacher - student;
jsd = two-way {C, D} JSD (the SDPO divergence family). Reciprocity in
logit space = state-dependent sign flip of delta.

Caveat: labels are forced as " <label>" continuations, whose tokenization
may differ from natural emission (seam effect, see
teacher_forcing.continuation_logprob) — absolute p_coop_* are NOT
comparable to behavioral cooperation rates; trust the deltas and JSDs,
where the seam is identical on both sides. Forced token ids are recorded
in metadata for audit.

Writes probe_a.json into --output-dir. Usage (container, 1 GPU):
    python3 -m moralgym_verl.eval.probe_a \
        --config configs/eval/harness/gemma2_9b/classic.yaml \
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

from moralgym_verl.eval.config import PROTOCOL_PRESETS
from moralgym_verl.eval.teacher_forcing import (
    PROBE_STATES, answer_logodds, chat_prefix, probe_setup, two_way_jsd,
)
from moralgym_verl.game.classic_games import FIXED_PAYOFFS
from moralgym_verl.game.prompts import build_prompt

logger = logging.getLogger(__name__)


def answer_token_probe(model, tokenizer, config, wrapper) -> Dict:
    """Label logodds shift + JSD per state, non-reasoning closer."""
    cfg = copy.deepcopy(config)
    cfg.reasoning = False
    results = {}
    for state, hist_a, hist_o in PROBE_STATES:
        game_prompt = build_prompt(cfg, hist_a, hist_o)
        student_ids = chat_prefix(tokenizer, game_prompt, model.device,
                                  cfg.enable_thinking)
        teacher_ids = chat_prefix(tokenizer, wrapper(game_prompt), model.device,
                                  cfg.enable_thinking)
        lo_s, lp_cs, lp_ds = answer_logodds(
            model, tokenizer, student_ids, cfg.coop_label, cfg.defect_label)
        lo_t, lp_ct, lp_dt = answer_logodds(
            model, tokenizer, teacher_ids, cfg.coop_label, cfg.defect_label)
        results[state] = {
            "logodds_student": lo_s,
            "logodds_teacher": lo_t,
            # Same quantity probe B calls answer_delta (teacher - student
            # shift on the answer token) — one name across both probes.
            "answer_delta": lo_t - lo_s,
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
    parser.add_argument("--model", default=None,
                        help="Override policy.model_name (as behavioral.py) "
                             "so the probe describes the cell's weights.")
    parser.add_argument("--protocol", default=None,
                        choices=sorted(PROTOCOL_PRESETS),
                        help="Cell's protocol preset (as behavioral.py). The "
                             "fabricated states make this probe a "
                             "single-decision instrument either way, but "
                             "applying the preset keeps its prompt built from "
                             "the same turn structure as the cell rather than "
                             "from the eval yaml's default.")
    parser.add_argument("--representation", default=None,
                        choices=["matrix", "prose", "list"],
                        help="Override prompt.representation (payoff block "
                             "rendering) for the probed cell.")
    parser.add_argument("--output-dir", default="results",
                        help="Run directory; writes probe_a.json into it.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Opponent is irrelevant here — the probe conditions on fabricated states.
    _, config, model, tokenizer, wrapper, metadata = probe_setup(args)

    logger.info("Probe A (answer-token) over %d states ...", len(PROBE_STATES))
    probe_a = answer_token_probe(model, tokenizer, config, wrapper)
    for state, r in probe_a.items():
        logger.info("  %s: p(C) %.2f -> %.2f  (answer_delta logodds %+.2f, jsd %.4f)",
                    state, r["p_coop_student"], r["p_coop_teacher"],
                    r["answer_delta"], r["jsd"])

    # Seam-caveat audit trail: the exact ids each label was forced as.
    metadata = {
        **metadata,
        "probe": "probe_a",
        "continuation_token_ids": {
            f" {label}": tokenizer(
                f" {label}", add_special_tokens=False
            ).input_ids
            for label in (config.coop_label, config.defect_label)
        },
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path_a = out_dir / "probe_a.json"
    with open(path_a, "w") as f:
        json.dump({"metadata": metadata, "probe_a": probe_a},
                  f, indent=2)
    logger.info("Probe A results saved to %s", path_a)


if __name__ == "__main__":
    main()
