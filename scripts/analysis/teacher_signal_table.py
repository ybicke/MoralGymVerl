#!/usr/bin/env python3.11
"""Session 1 deliverable: teacher-signal tables per game and moral value.

Reads eval_results/teacher_signal/*.json (behavioral runs from
eval_teacher_signal.sh and *_probe.json from eval/logprob_probe.py), keeps the
newest run per (game, moral_value), and prints three markdown tables per
game:

  1. Behavioral conditionals — P(C|opp C), P(D|opp D) per opponent, plus
     exploitation resistance vs always_cooperate and parse-fail. The
     'none' row is the student (plain-prompt) anchor; each wording row is
     a teacher generation run — read effects as wording-row minus none-row.
  2. State conditioning — P(C | agent_prev, opp_prev) for all four states
     CC/CD/DC/DD, measured vs the `random` opponent (visits all states
     evenly). Separates retaliation (o_prev=D) from forgiveness-after-
     own-defection (a_prev=D) exactly.
  3. Logprob probe — probe A answer-token Δ log-odds per fabricated state
     (positive = moral context pushes toward C) and probe B trace metrics.
     Reciprocity = sign flip between opp-C and opp-D states; uniformly
     positive Δ = unconditional-cooperator failure.

Login-node friendly (no torch):
  cd ~/MoralGymVerl && /usr/bin/python3.11 scripts/analysis/teacher_signal_table.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BEHAVIORAL_COLUMNS = [
    # (opponent, block, field, header)
    ("tit_for_tat", "cond_given_opp_c", "p_C", "TFT P(C|C)"),
    ("tit_for_tat", "cond_given_opp_d", "p_D", "TFT P(D|D)"),
    ("random", "cond_given_opp_c", "p_C", "RND P(C|C)"),
    ("random", "cond_given_opp_d", "p_D", "RND P(D|D)"),
    ("always_defect", "cond_given_opp_d", "p_D", "AD P(D|D)"),
    ("always_cooperate", "cond_given_opp_c", "p_C", "AC P(C|C)"),
]

STATES = ["(C,C)", "(C,D)", "(D,C)", "(D,D)"]        # (agent_prev, opp_prev)
PROBE_STATES = ["first", "CC", "CD", "DC", "DD"]


def load_latest(eval_dir: Path):
    """Three dicts keyed (game, moral_value): behavioral, probe A, probe B.
    Files are classified by content (works for per-run dirs and legacy
    flat files, incl. old combined _probe.json). Newest timestamp wins."""
    behavioral, probes_a, probes_b = {}, {}, {}
    randomized = []
    for path in sorted(eval_dir.rglob("*.json")):
        with open(path) as f:
            data = json.load(f)
        meta = data.get("metadata", {})
        # Representation-robustness runs (any non-fixed presentation axis)
        # and multi-turn transcript runs are listed separately, not mixed
        # into the standard single-round tables — point --eval-dir at
        # their stage subdirectory to tabulate them on their own.
        presentation = meta.get("eval_presentation") or {}
        if any(v != "fixed" for v in presentation.values()) or meta.get("transcript"):
            randomized.append(str(path.relative_to(eval_dir)))
            continue
        key = (meta.get("game_type"), meta.get("moral_value", "none"))
        stamp = meta.get("timestamp", "")
        targets = []
        if "opponents" in data:
            targets.append(behavioral)
        if data.get("answer_token_probe"):
            targets.append(probes_a)
        if data.get("trace_probe"):
            targets.append(probes_b)
        for target in targets:
            if key not in target or stamp > target[key][0]:
                target[key] = (stamp, str(path.relative_to(eval_dir)), data)
    if randomized:
        print("NOTE — randomized-presentation runs (excluded from tables, "
              "analyze separately):")
        for name in randomized:
            print(f"  {name}")
    return behavioral, probes_a, probes_b


def _fmt(value, n=None) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}" + (f" (n={n})" if n is not None else "")


def _opp_cell(opponents, opp, block, field) -> str:
    for entry in opponents:
        if entry.get("opponent") == opp:
            metrics = entry.get(block) or {}
            if metrics.get("n"):
                return _fmt(metrics[field], metrics["n"])
    return "—"


def _print_table(headers, rows) -> None:
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()


def _sorted_values(runs, game):
    return sorted((mv for g, mv in runs if g == game),
                  key=lambda v: (v != "none", v))


def behavioral_tables(game, runs) -> None:
    print("### Behavioral conditionals ('none' = student anchor)\n")
    rows = []
    for mv in _sorted_values(runs, game):
        _, fname, data = runs[(game, mv)]
        opps = data.get("opponents", [])
        parse = max((e.get("parse_failure_rate", 0.0) for e in opps), default=None)
        rows.append(
            [mv]
            + [_opp_cell(opps, o, b, f) for o, b, f, _ in BEHAVIORAL_COLUMNS]
            + [_fmt(parse), fname]
        )
    _print_table(["moral value"] + [h for *_, h in BEHAVIORAL_COLUMNS]
                 + ["parse-fail", "file"], rows)

    print("### State conditioning P(C | agent_prev, opp_prev) — vs random\n")
    rows = []
    for mv in _sorted_values(runs, game):
        _, _, data = runs[(game, mv)]
        state_cond = next(
            (e.get("state_conditioning") or {} for e in data.get("opponents", [])
             if e.get("opponent") == "random"), {})
        row = [mv]
        for state in STATES:
            metrics = state_cond.get(state) or {}
            row.append(_fmt(metrics.get("p_C"), metrics.get("n"))
                       if metrics.get("n") else "—")
        rows.append(row)
    _print_table(["moral value"] + [f"P(C|{s})" for s in STATES], rows)


def probe_tables(game, probes_a, probes_b) -> None:
    keys = {mv for g, mv in list(probes_a) + list(probes_b) if g == game}
    if not keys:
        return
    print("### Logprob probes — Δ log-odds toward C (teacher − student)\n")
    rows = []
    for mv in sorted(keys):
        a = (probes_a.get((game, mv)) or (None, None, {}))[2].get(
            "answer_token_probe") or {}
        b = (probes_b.get((game, mv)) or (None, None, {}))[2].get(
            "trace_probe") or {}
        row = [mv]
        for state in PROBE_STATES:
            entry = a.get(state)
            row.append(f"{entry['delta']:+.2f}" if entry else "—")

        # Trace probe: answer_delta averaged over opp-C vs opp-D states.
        def trace_mean(states):
            vals = [b[s]["answer_delta"]["mean"] for s in states
                    if b.get(s) and b[s]["answer_delta"]["mean"] is not None]
            return f"{sum(vals) / len(vals):+.2f}" if vals else "—"

        row += [trace_mean(["CC", "DC"]), trace_mean(["CD", "DD"])]
        rows.append(row)
    _print_table(
        ["moral value"] + [f"A:{s}" for s in PROBE_STATES]
        + ["B:ansΔ|opp C", "B:ansΔ|opp D"],
        rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path,
                        default=Path("eval_results/teacher_signal"))
    args = parser.parse_args()

    behavioral, probes_a, probes_b = load_latest(args.eval_dir)
    if not (behavioral or probes_a or probes_b):
        raise SystemExit(f"No result JSONs in {args.eval_dir}")

    games = sorted({g for g, _ in
                    list(behavioral) + list(probes_a) + list(probes_b)})
    for game in games:
        print(f"\n## {game}\n")
        if any(g == game for g, _ in behavioral):
            behavioral_tables(game, behavioral)
        probe_tables(game, probes_a, probes_b)


if __name__ == "__main__":
    main()
