#!/usr/bin/env python3.11
"""Training-side behavioral trajectory from verl rollout dumps.

Training already generated 256 rollouts per step and wrote them to
<run>/rollouts/<step>.jsonl (trainer.rollout_data_dir). This reads those
files -- no model is run, no GPU -- and reports, per window of W steps,
the cooperation rate in each fabricated previous-round state:

    steps   P(C|CC)  P(C|CD)  P(C|DC)  P(C|DD)  mean P(C|state)  illegal  n

Dense (every step, ~640 decisions per state per 20-step window) but
measured under TRAINING conditions (surface-randomized prompts, T=0.7
sampling), so it shows the SHAPE of learning -- when each state moves,
where it saturates or overshoots. The checkpoint eval
(specs/post_training.py) is the publishable number at a few steps
under the screen protocol; this is the curve that explains it.

Login node, stdlib only:
    /usr/bin/python3.11 scripts/analysis/training_trajectory.py \
        ~/logs_verl/runs/qwen_run2_200 [~/logs_verl/runs/grpo_deon_tft_200] \
        [--window 20] [--out analysis/trajectory.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from results_doc import (  # noqa: E402
    MISSING, Cell, Table, pct, state_label, to_markdown,
)

STATES = ("CC", "CD", "DC", "DD")


def load_decisions(run_dir: Path) -> List[Tuple[int, str, str]]:
    """(step, state, move) per rollout; move in {C, D, illegal}.
    State = fab_agent + fab_opp from the dumped ground truth."""
    out = []
    for f in sorted((run_dir / "rollouts").glob("*.jsonl"),
                    key=lambda p: int(p.stem)):
        for line in open(f):
            row = json.loads(line)
            gts = json.loads(row["gts"])
            state = gts["fab_agent"] + gts["fab_opp"]
            if float(row["parse_fail_rate"]) > 0:
                move = "illegal"
            else:
                move = "C" if float(row["cooperation_rate"]) > 0 else "D"
            out.append((int(row["step"]), state, move))
    if not out:
        raise SystemExit(f"{run_dir}: no rollouts/*.jsonl found")
    return out


def windows(decisions, window: int) -> List[Dict]:
    """Per window: p_C per state (illegal excluded from the denominator),
    the mean of the four state rates, illegal rate, n decisions.

    NOTE the mean is unweighted: training samples the fabricated state
    at random, so the four states are NOT equally represented in a
    window (181-200 of qwen_run2_200: n = 1407/1211/1449/1038). Weighting
    them equally is deliberate -- it is what makes the row comparable to
    the balanced checkpoint eval -- but it is not the pooled cooperation
    rate of the window, which the unequal denominators would tilt."""
    acc: Dict[int, Dict] = defaultdict(
        lambda: {"c": defaultdict(int), "t": defaultdict(int),
                 "illegal": 0, "n": 0})
    for step, state, move in decisions:
        w = (step - 1) // window
        a = acc[w]
        a["n"] += 1
        if move == "illegal":
            a["illegal"] += 1
            continue
        a["t"][state] += 1
        a["c"][state] += move == "C"
    rows = []
    for w in sorted(acc):
        a = acc[w]
        p = {s: (a["c"][s] / a["t"][s]) if a["t"][s] else None
             for s in STATES}
        present = [v for v in p.values() if v is not None]
        rows.append({"first": w * window + 1, "last": (w + 1) * window,
                     "p": p, "pooled": sum(present) / len(present),
                     "illegal": a["illegal"] / a["n"], "n": a["n"]})
    return rows


def trajectory_table(run_name: str, rows: List[Dict], window: int,
                     vocab: Dict[int, Dict] = None) -> Table:
    """vocab (optional): window index -> {n, vocab, overlap}, appended as
    two columns so the vocabulary's onset can be read against the
    behaviour in the same row."""
    body = []
    for i, r in enumerate(rows):
        cells = [Cell(pct(r["p"][s])) if r["p"][s] is not None
                 else Cell(MISSING) for s in STATES]
        cells += [Cell(pct(r["pooled"]))]
        if vocab is not None:
            v = vocab.get(i)
            cells += ([Cell(f"{100 * v['vocab'] / v['n']:.0f}"),
                       Cell(f"{100 * v['overlap'] / v['n']:.0f}")]
                      if v and v["n"] else [Cell(MISSING), Cell(MISSING)])
        cells += [Cell(f"{100 * r['illegal']:.1f}"), Cell(str(r["n"]))]
        body.append((f"{r['first']}–{r['last']}", cells))
    return Table(
        key=f"trajectory-{run_name}",
        title=f"Training trajectory — {run_name}",
        caption=(
            "Cooperation rate (\\%) by fabricated previous state over "
            f"training, in {window}-step windows ({256 * window} "
            "decisions per window). Illegal (unparseable) moves are "
            "excluded from the state denominators and reported as a "
            "rate. mean P(C$\\mid$state) = UNWEIGHTED mean of the four "
            "state rates -- the states are unbalanced here, so this is "
            "a mean of rates, NOT the window's pooled cooperation "
            "rate. normative \\% and recites \\% are the trace measures "
            "defined in chapter 1, computed over EVERY rollout in the "
            "window: they put the onset of the moral vocabulary next to "
            "the behaviour it accompanies."),
        stub="Steps",
        col_groups=[(None, [f"P(C$\\mid${state_label(s)})"]) for s in STATES]
        + [(None, ["mean P(C$\\mid$state)"])]
        + ([(None, ["normative \\%"]), (None, ["recites \\%"])]
           if vocab is not None else [])
        + [(None, ["illegal \\%"]), (None, ["n"])],
        panels=[(None, body)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", nargs="+", type=Path,
                        help="run directories containing rollouts/")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--out", type=Path, default=None,
                        help="write the markdown here (default: stdout)")
    args = parser.parse_args()
    md = []
    for run in args.runs:
        rows = windows(load_decisions(run), args.window)
        md.append(to_markdown(trajectory_table(run.name, rows, args.window)))
    text = "\n".join(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"saved -> {args.out}")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
