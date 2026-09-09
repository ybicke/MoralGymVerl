#!/usr/bin/env python3
"""Pretty-print verl rollout dumps (trainer.rollout_data_dir JSONL files).

One block per rollout: fabricated state, parsed action, score, and the
model's reasoning trace. Stdlib-only — runs on the login node:

    /usr/bin/python3.11 scripts/inspect_rollouts.py \\
        ~/logs_verl/runs/<run>/rollouts/10.jsonl [-n 5] [--prompt] [--full]

    -n N       show N rollouts (default 5; 0 = all)
    --prompt   also print the game prompt (input)
    --full     don't truncate the reasoning trace
    --state X  only rollouts with fabricated state X (CC, CD, DC, DD
               = fab_agent + fab_opp)
    --action X only rollouts where the parsed action is C, D, or FAIL
"""

from __future__ import annotations

import argparse
import json
import textwrap


def parsed_action(row: dict) -> str:
    if row.get("parse_fail_rate") == 1.0:
        return "FAIL"
    return "C" if row.get("cooperation_rate") == 1.0 else "D"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--prompt", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--state", choices=["CC", "CD", "DC", "DD"])
    ap.add_argument("--action", choices=["C", "D", "FAIL"])
    ap.add_argument(
        "--oneline",
        action="store_true",
        help="one row per rollout: state, action, score, end of the trace",
    )
    args = ap.parse_args()

    shown = 0
    total = 0
    for line in open(args.jsonl):
        row = json.loads(line)
        total += 1
        gts = json.loads(row["gts"]) if isinstance(row["gts"], str) else row["gts"]
        state = gts.get("fab_agent", "?") + gts.get("fab_opp", "?")
        act = parsed_action(row)
        if args.state and state != args.state:
            continue
        if args.action and act != args.action:
            continue
        if args.n and shown >= args.n:
            continue  # keep counting total/filters but stop printing
        shown += 1

        if args.oneline:
            # The trace END is the informative part (the Action line and the
            # sentence justifying it); one row per rollout, NeMo-RL style.
            tail = " ".join(row["output"].split())[-110:]
            print(f"{state}  {act}  {row['score']:+.2f}  …{tail}")
            continue

        label = {"C": gts["coop_label"], "D": gts["defect_label"], "FAIL": "-"}[act]
        print(f"{'=' * 78}")
        print(
            f"#{shown}  step={row['step']}  state={state} "
            f"(you={gts.get('fab_agent')}, opp={gts.get('fab_opp')})  "
            f"action={act} ({label})  score={row['score']:+.2f}"
        )
        if args.prompt:
            print(f"{'-' * 30} prompt {'-' * 30}")
            print(textwrap.indent(row["input"].strip(), "  "))
        print(f"{'-' * 30} reasoning {'-' * 27}")
        out = row["output"].strip()
        if not args.full and len(out) > 1200:
            out = out[:1200] + f"\n  [... {len(row['output']) - 1200} more chars, use --full]"
        print(textwrap.indent(out, "  "))
        print()

    print(f"[{shown} shown of {total} rollouts in {args.jsonl}]")


if __name__ == "__main__":
    main()
