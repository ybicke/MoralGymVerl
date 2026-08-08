#!/usr/bin/env python3.11
"""Stage 1b multi-turn dynamics from episode_moves: recovery, sucker, drift.

Single-round state tables can't see dynamics — whether a defection
spiral ends, whether forgiveness is farmable, whether cooperation decays
against a saint. This reads the 5-round move sequences recorded in
behavioral.json (`episode_moves`, strict parser live at runtime) for
every conversation-mode run under --eval-dir and prints, per condition (moral
value) x opponent:

  1. Per-round cooperation rate (recomputed from episode_moves; illegal
     moves excluded from denominators and reported).
  2. Recovery vs tit_for_tat / noisy_tft: every round t where the
     opponent defected is an event; recovery = first later round u with
     mutual CC. P(recover within 1 / 2 rounds / by horizon), mean
     rounds-to-recover among recovered. Events overlap within an episode
     (a 3-round spiral = 3 events), so n(events) > n(episodes); treat as
     a descriptive rate, not independent trials.
  3. Sucker rate per round vs always_defect — any C after round 1 is
     farmed forgiveness.
  4. Drift vs always_cooperate: per-round P(D) and the round5-round1
     delta — temptation drift against unconditional cooperation.

Usage (login node):
  cd ~/MoralGymVerl && /usr/bin/python3.11 scripts/analysis/recovery_rate.py \
      [--eval-dir eval_results/teacher_signal/multi_round]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RECOVERY_OPPONENTS = ["tit_for_tat", "noisy_tft"]


def load_runs(eval_dir: Path) -> dict[str, dict]:
    """{moral_value: opponent_block_by_name}, newest run per value."""
    runs: dict[str, tuple[str, dict]] = {}
    for path in sorted(eval_dir.rglob("behavioral.json")):
        data = json.loads(path.read_text())
        meta = data["metadata"]
        if not meta.get("conversation") and meta.get("num_rounds", 1) == 1:
            continue
        stamp = meta.get("timestamp", "")
        value = meta.get("moral_value", "none")
        if value not in runs or stamp > runs[value][0]:
            runs[value] = (stamp, {o["opponent"]: o for o in data["opponents"]})
    return {v: opps for v, (_, opps) in runs.items()}


def _sorted_values(runs) -> list[str]:
    return sorted(runs, key=lambda v: (v != "none", v))


def _print_table(headers, rows) -> None:
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()


def per_round_rate(episodes: list[dict], move: str, rnd: int) -> str:
    legal = [ep["agent"][rnd] for ep in episodes
             if ep["agent"][rnd] != "illegal"]
    return f"{sum(m == move for m in legal) / len(legal):.0%}" if legal else "—"


def per_round_tables(runs: dict[str, dict], num_rounds: int) -> None:
    opponents = sorted({o for opps in runs.values() for o in opps})
    for opp in opponents:
        print(f"### P(C) per round vs {opp}\n")
        rows = []
        for value in _sorted_values(runs):
            block = runs[value].get(opp)
            if block is None:
                continue
            episodes = block["episode_moves"]
            illegal = sum(m == "illegal" for ep in episodes for m in ep["agent"])
            rows.append([value]
                        + [per_round_rate(episodes, "C", r)
                           for r in range(num_rounds)]
                        + [f"{illegal / (len(episodes) * num_rounds):.0%}"])
        _print_table(["moral value"]
                     + [f"r{r + 1}" for r in range(num_rounds)]
                     + ["illegal"], rows)


def recovery_stats(episodes: list[dict]) -> dict:
    """Opponent-defection events -> time to next mutual CC."""
    events = []                       # rounds-to-recover, None = never
    for ep in episodes:
        agent, opp = ep["agent"], ep["opp"]
        for t, opp_move in enumerate(opp[:-1]):
            if opp_move != "D":
                continue
            lag = next((u - t for u in range(t + 1, len(opp))
                        if agent[u] == "C" and opp[u] == "C"), None)
            events.append(lag)
    recovered = [lag for lag in events if lag is not None]
    return {
        "n": len(events),
        "within1": sum(lag <= 1 for lag in recovered),
        "within2": sum(lag <= 2 for lag in recovered),
        "ever": len(recovered),
        "mean_lag": (sum(recovered) / len(recovered)) if recovered else None,
    }


def recovery_tables(runs: dict[str, dict]) -> None:
    for opp in RECOVERY_OPPONENTS:
        if not any(opp in opps for opps in runs.values()):
            continue
        print(f"### Recovery after opponent defection vs {opp}\n")
        rows = []
        for value in _sorted_values(runs):
            block = runs[value].get(opp)
            if block is None:
                continue
            s = recovery_stats(block["episode_moves"])
            if not s["n"]:
                rows.append([value, "0", "—", "—", "—", "—"])
                continue
            rows.append([
                value, str(s["n"]),
                f"{s['within1'] / s['n']:.0%}",
                f"{s['within2'] / s['n']:.0%}",
                f"{s['ever'] / s['n']:.0%}",
                f"{s['mean_lag']:.1f}" if s["mean_lag"] is not None else "—",
            ])
        _print_table(["moral value", "opp-D events", "CC within 1",
                      "CC within 2", "CC by horizon", "mean rounds"], rows)


def exploitation_tables(runs: dict[str, dict], num_rounds: int) -> None:
    if any("always_defect" in opps for opps in runs.values()):
        print("### Sucker rate per round vs always_defect "
              "(P(C) — forgiveness being farmed)\n")
        rows = []
        for value in _sorted_values(runs):
            block = runs[value].get("always_defect")
            if block is None:
                continue
            episodes = block["episode_moves"]
            per_round = [per_round_rate(episodes, "C", r)
                         for r in range(num_rounds)]
            sucker = block["sucker_rate"]
            rows.append([value] + per_round
                        + [f"{sucker:.0%}" if sucker is not None else "n/a"])
        _print_table(["moral value"]
                     + [f"r{r + 1}" for r in range(num_rounds)]
                     + ["overall sucker"], rows)

    if any("always_cooperate" in opps for opps in runs.values()):
        print("### Drift vs always_cooperate (P(D) — temptation drift)\n")
        rows = []
        for value in _sorted_values(runs):
            block = runs[value].get("always_cooperate")
            if block is None:
                continue
            episodes = block["episode_moves"]
            per_round = [per_round_rate(episodes, "D", r)
                         for r in range(num_rounds)]

            def rate(rnd):
                legal = [ep["agent"][rnd] for ep in episodes
                         if ep["agent"][rnd] != "illegal"]
                return sum(m == "D" for m in legal) / len(legal) if legal else None

            first, last = rate(0), rate(num_rounds - 1)
            drift = (f"{last - first:+.0%}"
                     if first is not None and last is not None else "—")
            rows.append([value] + per_round + [drift])
        _print_table(["moral value"]
                     + [f"r{r + 1}" for r in range(num_rounds)]
                     + ["drift r5−r1"], rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval-dir", type=Path,
        default=Path("eval_results/teacher_signal/multi_round"))
    args = parser.parse_args()

    runs = load_runs(args.eval_dir)
    if not runs:
        raise SystemExit(f"No conversation-mode runs under {args.eval_dir}")
    num_rounds = max(len(ep["agent"]) for opps in runs.values()
                     for block in opps.values()
                     for ep in block["episode_moves"])

    per_round_tables(runs, num_rounds)
    recovery_tables(runs)
    exploitation_tables(runs, num_rounds)


if __name__ == "__main__":
    main()
