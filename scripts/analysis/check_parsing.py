#!/usr/bin/env python3.11
"""Audit parsing of saved reasoning traces (login-node, no torch).

Uses the REAL strict parser (game.prompts_reasoning.parse_action_structured
— well-formed `Action: <label>` only, no lenient fallback) and, for
comparison, replays the OLD parser (optional separator + lenient
substring fallback) to show how many decisions the strictness change
affects:

  structured — clean `Action: <label>` found (trustworthy)
  illegal    — no well-formed Action line -> parse failure (no signal)
  old-differ — decisions where the old parser would have RETURNED an
               action that differs from the strict result (wrong-signal
               candidates the strict parser now rejects or corrects)
  multi      — traces with MORE THAN ONE decision statement (`Action:
               <label>`); option headers like `**Action3:**` don't count
  1st!=last  — multi traces whose first and last decision disagree (the
               parser takes the last = final commitment after reasoning)

Usage:
  cd ~/MoralGymVerl && PYTHONPATH=src /usr/bin/python3.11 \
      scripts/analysis/check_parsing.py [--eval-dir DIR] [--show-illegal]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from moralgym_verl.game.environment import EpisodeConfig  # noqa: E402
from moralgym_verl.game.prompts import parse_action_lenient  # noqa: E402
from moralgym_verl.game.prompts_reasoning import parse_action_structured  # noqa: E402

CFG = EpisodeConfig(
    game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
    opponent="random", num_rounds=1,
    coop_label="action3", defect_label="action4",
)

# The pre-2026-07-14 parser: optional separator + lenient fallback.
_OLD_ACTION_RE = re.compile(r"[Aa]ction\s*[:\-]?\s*\**\s*([A-Za-z0-9_]+)")


def old_parse(response: str) -> str | None:
    matches = list(_OLD_ACTION_RE.finditer(response))
    if matches:
        captured = matches[-1].group(1).strip().rstrip(".,!?;:").upper()
        coop, defect = CFG.coop_label.upper(), CFG.defect_label.upper()
        if captured == coop:
            return "C"
        if captured == defect:
            return "D"
        fc, fd = coop in captured, defect in captured
        if fc and not fd:
            return "C"
        if fd and not fc:
            return "D"
    return parse_action_lenient(response, CFG)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path,
                        default=Path("eval_results/teacher_signal"))
    parser.add_argument("--show-illegal", action="store_true",
                        help="Print the tail of every illegal trace")
    args = parser.parse_args()

    files = sorted(args.eval_dir.rglob("*.responses.jsonl"))
    if not files:
        raise SystemExit(f"No .responses.jsonl files in {args.eval_dir}")

    from moralgym_verl.game.prompts_reasoning import _ACTION_RE

    print(f"{'file':<62} {'n':>5} {'structured':>11} {'illegal':>8}"
          f" {'old-differ':>11} {'multi':>6} {'1st!=last':>10}")
    for path in files:
        n = structured = illegal = old_differ = multi = flip = 0
        illegal_traces = []
        with open(path) as f:
            for line in f:
                raw = json.loads(line)["raw"]
                n += 1
                new = parse_action_structured(raw, CFG)
                if new is None:
                    illegal += 1
                    illegal_traces.append(raw)
                else:
                    structured += 1
                if old_parse(raw) != new:
                    old_differ += 1
                decisions = [m.group(1).strip().rstrip(".,!?;:").lower()
                             for m in _ACTION_RE.finditer(raw)]
                decisions = [d for d in decisions
                             if d in (CFG.coop_label, CFG.defect_label)]
                if len(decisions) > 1:
                    multi += 1
                    if decisions[0] != decisions[-1]:
                        flip += 1
        name = str(path.relative_to(args.eval_dir))
        print(f"{name:<62} {n:>5} {structured / n:>10.0%}"
              f" {illegal / n:>7.0%} {old_differ / n:>10.0%}"
              f" {multi:>6} {flip:>10}")
        if args.show_illegal:
            for raw in illegal_traces:
                print(f"  ILLEGAL ...{raw[-120:]!r}")


if __name__ == "__main__":
    main()
