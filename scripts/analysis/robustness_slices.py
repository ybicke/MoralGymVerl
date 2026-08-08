#!/usr/bin/env python3.11
"""Robustness runs R1/R2: per-state tables vs the fixed baseline + per-axis slices.

R1 (2761025) randomizes presentation structure (matrix layout, opener/
closer label order, row/col role, payoff sampling) with fixed
action3/action4 labels; R2 (2761026) randomizes only the action labels
(random letters). Both are `none` (student prompt) runs — the question is
whether the base policy's state conditioning is a property of the game or
of one frozen prompt string. R2 additionally gates dataset
`randomize_labels: true`.

Per episode, the fabricated state is read from the saved prompt
("...you played <lab> and they played <lab>...") and mapped through that
episode's own coop/defect labels (handles R2's random letters); the move
comes from behavioral.json's `episode_moves` (strict parser was live at
runtime). The recomputed 4-state table is cross-checked against the
runtime `state_conditioning` block, so prompt<->episode alignment is
verified, not assumed.

The baseline per-state table is rebuilt from the fixed-presentation
`none` run's traces with the strict parser (its behavioral.json holds
stale old-parser metrics — see rebuild_state_table.py).

Slices: each presentation facet that varies within a run (matrix_layout,
agent_is_row, opener/closer label order, greed T-R and fear P-S median
splits, R2 label alphabetical order) gets a row per level with
P(C|opp_prev=C), P(C|opp_prev=D) and the reciprocity gap. n/state/level
is small (~10-30) -> read slices as bias screens (+-15-30pp noise), not
precise estimates.

Usage (login node):
  cd ~/MoralGymVerl && /usr/bin/python3.11 scripts/analysis/robustness_slices.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from moralgym_verl.game.environment import EpisodeConfig  # noqa: E402
from moralgym_verl.game.prompts_reasoning import parse_action_structured  # noqa: E402

STATES = ["(C,C)", "(C,D)", "(D,C)", "(D,D)"]
_STATE_RE = re.compile(r"you played (\w+) and\s+they played (\w+)")

BASELINE_CFG = EpisodeConfig(
    game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
    opponent="random", num_rounds=1,
    coop_label="action3", defect_label="action4",
)


def episode_records(run_dir: Path) -> list[dict]:
    """One dict per episode: state, move (C/D/illegal), presentation."""
    data = json.loads((run_dir / "behavioral.json").read_text())
    moves = data["opponents"][0]["episode_moves"]
    with open(run_dir / "behavioral.responses.jsonl") as f:
        prompts = [json.loads(line)["prompt"] for line in f]
    if len(prompts) != len(moves):
        raise SystemExit(f"{run_dir.name}: {len(prompts)} prompts vs "
                         f"{len(moves)} episode_moves — cannot align")
    records = []
    for prompt, ep in zip(prompts, moves):
        pres = ep["presentation"]
        match = _STATE_RE.search(prompt)
        label = {pres["coop_label"]: "C", pres["defect_label"]: "D"}
        state = f"({label[match.group(1)]},{label[match.group(2)]})"
        records.append(
            {"state": state, "move": ep["agent"][0], "presentation": pres})

    # Alignment check: recomputed table must equal the runtime one.
    runtime = data["opponents"][0]["state_conditioning"]
    for state in STATES:
        recs = [r for r in records if r["state"] == state]
        p_c = sum(r["move"] == "C" for r in recs) / len(recs)
        if abs(p_c - runtime[state]["p_C"]) > 1e-9 or len(recs) != runtime[state]["n"]:
            raise SystemExit(f"{run_dir.name}: recomputed {state} "
                             f"({p_c:.3f}, n={len(recs)}) != runtime "
                             f"({runtime[state]['p_C']:.3f}, n={runtime[state]['n']})")
    return records


def baseline_state_table(run_dir: Path) -> dict[str, float]:
    """P(C|state) rebuilt from traces with the strict parser."""
    cells: dict[str, list] = defaultdict(list)
    with open(run_dir / "behavioral.responses.jsonl") as f:
        for line in f:
            rec = json.loads(line)
            match = _STATE_RE.search(rec["prompt"])
            state = f"({'CD'[match.group(1) == 'action4']}," \
                    f"{'CD'[match.group(2) == 'action4']})"
            cells[state].append(parse_action_structured(rec["raw"], BASELINE_CFG))
    return {s: sum(1 for m in cells[s] if m == "C")
               / sum(1 for m in cells[s] if m is not None)
            for s in STATES}


def state_row(records: list[dict]) -> list[str]:
    row = []
    for state in STATES:
        legal = [r["move"] for r in records
                 if r["state"] == state and r["move"] != "illegal"]
        row.append(f"{sum(m == 'C' for m in legal) / len(legal):.0%} "
                   f"(n={len(legal)})" if legal else "—")
    return row


def slice_axes(records: list[dict]) -> dict[str, callable]:
    """Facet extractors; only facets with >1 observed level are kept."""
    def greed(p):
        return p["payoffs"]["T"] - p["payoffs"]["R"]

    def fear(p):
        return p["payoffs"]["P"] - p["payoffs"]["S"]

    greed_med = median(greed(r["presentation"]) for r in records)
    fear_med = median(fear(r["presentation"]) for r in records)
    axes = {
        "matrix_layout": lambda p: f"layout={p['matrix_layout']}",
        "role": lambda p: "row" if p["agent_is_row"] else "column",
        "opener_order": lambda p: ("coop-first"
                                   if p["opener_order"][0] == p["coop_label"]
                                   else "defect-first"),
        "closer_order": lambda p: ("coop-first"
                                   if p["closer_order"][0] == p["coop_label"]
                                   else "defect-first"),
        "greed T-R": lambda p, m=greed_med: f"T-R{'>' if greed(p) > m else '<='}{m}",
        "fear P-S": lambda p, m=fear_med: f"P-S{'>' if fear(p) > m else '<='}{m}",
        "label order": lambda p: ("coop alphabetically first"
                                  if p["coop_label"] < p["defect_label"]
                                  else "defect alphabetically first"),
    }
    return {name: fn for name, fn in axes.items()
            if len({fn(r["presentation"]) for r in records}) > 1}


def print_slices(records: list[dict]) -> None:
    print("| axis | level | n | illegal | P(C) | P(C\\|opp C) | P(C\\|opp D) | recip gap |")
    print("|---|---|---|---|---|---|---|---|")
    for axis, fn in slice_axes(records).items():
        by_level = defaultdict(list)
        for r in records:
            by_level[fn(r["presentation"])].append(r)
        for level in sorted(by_level):
            recs = by_level[level]
            legal = [r for r in recs if r["move"] != "illegal"]

            def p_c(subset):
                sub = [r for r in legal if r["state"] in subset]
                return (sum(r["move"] == "C" for r in sub) / len(sub)
                        if sub else None)

            opp_c, opp_d = p_c({"(C,C)", "(D,C)"}), p_c({"(C,D)", "(D,D)"})
            gap = (f"{opp_c - opp_d:+.0%}"
                   if opp_c is not None and opp_d is not None else "—")
            print(f"| {axis} | {level} | {len(recs)} "
                  f"| {1 - len(legal) / len(recs):.0%} "
                  f"| {p_c(set(STATES)):.0%} | {opp_c:.0%} | {opp_d:.0%} "
                  f"| {gap} |")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robustness-dir", type=Path,
                        default=Path("eval_results/teacher_signal/robustness"))
    parser.add_argument(
        "--baseline-dir", type=Path,
        default=Path("eval_results/teacher_signal/single_round"
                     "/prisoners_dilemma__none_2759076"))
    args = parser.parse_args()

    baseline = baseline_state_table(args.baseline_dir)
    print(f"Baseline (fixed presentation, {args.baseline_dir.name}, "
          "strict parser): "
          + "  ".join(f"{s} {baseline[s]:.0%}" for s in STATES) + "\n")

    for run_dir in sorted(args.robustness_dir.iterdir()):
        if not (run_dir / "behavioral.json").exists():
            continue
        meta = json.loads((run_dir / "behavioral.json").read_text())["metadata"]
        records = episode_records(run_dir)
        illegal = sum(r["move"] == "illegal" for r in records) / len(records)
        randomized = [k for k, v in meta["eval_presentation"].items()
                      if v != "fixed"]
        print(f"## {run_dir.name} — randomized: {', '.join(randomized)} "
              f"(alignment check passed)\n")
        print(f"illegal rate: {illegal:.1%} ({len(records)} episodes)\n")

        print("| | " + " | ".join(f"P(C\\|{s})" for s in STATES) + " |")
        print("|---|" + "---|" * len(STATES))
        print("| this run | " + " | ".join(state_row(records)) + " |")
        deltas = []
        for state in STATES:
            legal = [r["move"] for r in records
                     if r["state"] == state and r["move"] != "illegal"]
            deltas.append(
                f"{sum(m == 'C' for m in legal) / len(legal) - baseline[state]:+.0%}")
        print("| Δ vs fixed baseline | " + " | ".join(deltas) + " |\n")
        print_slices(records)


if __name__ == "__main__":
    main()
