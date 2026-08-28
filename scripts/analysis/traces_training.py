#!/usr/bin/env python3.11
"""Training traces: verbatim exemplars along the online rollouts.

Reads rollouts/<step>.jsonl of a training run -- the TRAINING surface
(labels action1/action2, randomized layout and role, fabricated previous
state sampled at random, so states are unbalanced). Not comparable
cell-by-cell with the checkpoint evals; read for the shape over steps.

Output: traces_training_<experiment>.md -- per step, a rate table by
fabricated state (n, P(C), normative %, recites %, overlap median / max),
one verbatim exemplar per state (the SHORTEST of K traces drawn with a
fixed seed: readable, content-blind) and the step's longest recitation.
Rates are defined once in trace_measures.py; annotation goes in
analysis_<experiment>.md.

Login node, stdlib only:
    /usr/bin/python3.11 scripts/analysis/traces_training.py \
        --rollouts ~/logs_verl/runs/qwen_run2_200 --steps 20,40,...,200 \
        --out eval_results/post_training/qwen3-8b-pd-sdpo-deon-repair-gen/analysis/traces_training_qwen3-8b-pd-sdpo-deon-repair-gen.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trace_measures import from_rollouts, parse_steps, render_exemplars  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rollouts", type=Path, required=True, help="run dir with rollouts/")
    ap.add_argument("--steps", default="20,40,...,200", help="'a,b,...,end' expands; plain lists pass")
    ap.add_argument("--principle", default="deontological+repair+generosity")
    ap.add_argument("--k", type=int, default=8, help="sample size per (step, state); shortest is shown")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-chars", type=int, default=2500)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    steps = parse_steps(a.steps)
    traces = from_rollouts(a.rollouts, steps)
    title = f"Training traces: {a.rollouts.name}"
    surface = ("Online rollouts (`rollouts/<step>.jsonl`), TRAINING surface: labels "
               "`action1`/`action2`, randomized layout and role, fabricated previous "
               "state sampled at random (unbalanced). Not comparable cell-by-cell "
               "with the checkpoint evals; read for the shape over steps.")
    text = render_exemplars(title, surface, steps, traces, a.principle, a.k, a.seed, a.max_chars)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    print(f"saved -> {a.out}  ({len(traces)} traces, steps {steps})")


if __name__ == "__main__":
    main()
