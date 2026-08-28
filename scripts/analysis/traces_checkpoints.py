#!/usr/bin/env python3.11
"""Checkpoint traces: verbatim exemplars from the checkpoint evals.

Reads the checkpoint-eval cells of a post-training group
(cells/*/behavioral.responses.jsonl): screen surface, balanced states,
the same episodes as Table P1 of results_<experiment>.md. Steps are the
evaluated checkpoints.

Output: traces_checkpoints_<experiment>.md -- per step, a rate table by
fabricated state (n, P(C), normative %, recites %, overlap median / max),
one verbatim exemplar per state (the SHORTEST of K traces drawn with a
fixed seed: readable, content-blind) and the step's longest recitation.
Rates are defined once in trace_measures.py; annotation goes in
analysis_<experiment>.md.

Login node, stdlib only:
    /usr/bin/python3.11 scripts/analysis/traces_checkpoints.py \
        eval_results/post_training/qwen3-8b-pd-sdpo-deon-repair-gen \
        --out eval_results/post_training/qwen3-8b-pd-sdpo-deon-repair-gen/analysis/traces_checkpoints_qwen3-8b-pd-sdpo-deon-repair-gen.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trace_measures import from_cells, render_exemplars  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("group", type=Path, help="checkpoint-eval group dir")
    ap.add_argument("--principle", default="deontological+repair+generosity")
    ap.add_argument("--k", type=int, default=8, help="sample size per (step, state); shortest is shown")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-chars", type=int, default=2500)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    traces = from_cells(a.group)
    steps = sorted({t.step for t in traces})
    title = f"Checkpoint traces: {a.group.name}"
    surface = ("Checkpoint-eval cells (`behavioral.responses.jsonl`), screen surface: "
               "labels `action3`/`action4`, fixed layout, balanced states, 100 "
               "decisions per state; same episodes as Table P1 of results_*.md.")
    text = render_exemplars(title, surface, steps, traces, a.principle, a.k, a.seed, a.max_chars)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    print(f"saved -> {a.out}  ({len(traces)} traces, steps {steps})")


if __name__ == "__main__":
    main()
