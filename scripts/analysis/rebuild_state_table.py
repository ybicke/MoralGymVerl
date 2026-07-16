#!/usr/bin/env python3.11
"""Rebuild the 4-state behavioral tables offline from saved traces.

Why: the Stage 1a/1c runs of 2026-07-14 computed their metrics with the
old parser (optional separator + lenient fallback), which mis-assigned
1-12% of decisions (see check_parsing.py's old-differ column). Every
(prompt, trace) pair is saved, so the corrected tables can be rebuilt
without GPU: re-parse each trace with the strict parser and read the
fabricated state from the saved prompt.

Single-round hist runs only (each record = one conditioned decision).

Usage:
  cd ~/MoralGymVerl && /usr/bin/python3.11 scripts/analysis/rebuild_state_table.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from moralgym_verl.game.environment import EpisodeConfig  # noqa: E402
from moralgym_verl.game.prompts_reasoning import parse_action_structured  # noqa: E402

CFG = EpisodeConfig(
    game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
    opponent="random", num_rounds=1,
    coop_label="action3", defect_label="action4",
)

# Fabricated-history sentence in the saved prompt (eval fixed labels).
_STATE_RE = re.compile(
    r"you played (action[34]) and\s+they played (action[34])")
_LABEL = {"action3": "C", "action4": "D"}
STATES = ["(C,C)", "(C,D)", "(D,C)", "(D,D)"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path,
                        default=Path("eval_results/teacher_signal"))
    args = parser.parse_args()

    # {(game, value): {state: [moves]}}
    cells: dict = defaultdict(lambda: defaultdict(list))
    for path in sorted(args.eval_dir.rglob("*.responses.jsonl")):
        run_dir = path.parent.name          # <game>__<value>_<jobid>
        if "__" not in run_dir:
            continue                        # legacy flat smoke files
        game, rest = run_dir.split("__", 1)
        value = rest.rsplit("_", 1)[0]
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                m = _STATE_RE.search(rec["prompt"])
                if not m:
                    continue                # no fabricated state (not 1a/1c)
                state = f"({_LABEL[m.group(1)]},{_LABEL[m.group(2)]})"
                action = parse_action_structured(rec["raw"], CFG)
                cells[(game, value)][state].append(action)

    games = sorted({g for g, _ in cells})
    for game in games:
        print(f"\n## {game} — corrected P(C | agent_prev, opp_prev), strict parser\n")
        headers = ["moral value"] + [f"P(C|{s})" for s in STATES] + ["illegal"]
        print("| " + " | ".join(headers) + " |")
        print("|" + "---|" * len(headers))
        values = sorted((v for g, v in cells if g == game),
                        key=lambda v: (v != "none", v))
        for value in values:
            row = [value]
            total = illegal = 0
            for state in STATES:
                moves = cells[(game, value)][state]
                legal = [mv for mv in moves if mv is not None]
                total += len(moves)
                illegal += len(moves) - len(legal)
                row.append(
                    f"{sum(1 for mv in legal if mv == 'C') / len(legal):.0%}"
                    f" (n={len(legal)})" if legal else "—")
            row.append(f"{illegal / total:.0%}" if total else "—")
            print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
