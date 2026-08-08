"""Cross-arm summary tables for teacher-signal eval cells.

One section per experiment, cleanly separated (skip any via --section):

    behavioral  behavioral.json          per opponent: cooperation,
                                         reciprocity/forgiveness
                                         conditionals, parse failures
    probe_a     probe_a.json             per state: deterministic label
                                         log-odds delta, p(C) shift
    probe_b     probe_b.json (+traces)   per state: token_delta,
                                         token_jsd, answer_delta with
                                         C-ward/D-ward mode split

Arms (= moral-value runs) become columns (probes) or row groups
(behavioral); states/opponents are rows. Cells missing an input file
are skipped per section, so mixed groups (e.g. a 'none' arm without
probes) print fine.

Login-node friendly (stdlib only, no torch):
    /usr/bin/python3.11 scripts/analysis/summarize_eval_cells.py \
        eval_results/teacher_signal/wording_screen
    # explicit run dirs and/or a single section:
    ... summarize_eval_cells.py <run_dir> [...] --section probe_b
    # persist alongside the cells (gitignored): add --save
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

STATES = ("first", "CC", "CD", "DC", "DD")
PROBE_B_METRICS = ("token_delta", "token_jsd", "answer_delta")
SECTIONS = ("behavioral", "probe_a", "probe_b")


# ---------------------------------------------------------------- shared

def discover_run_dirs(paths: List[Path]) -> List[Path]:
    """Explicit run dirs, or group dirs scanned for behavioral.json."""
    run_dirs: List[Path] = []
    for p in paths:
        if (p / "behavioral.json").exists():
            run_dirs.append(p)
        else:
            run_dirs.extend(sorted(d.parent for d in p.glob("*/behavioral.json")))
    if not run_dirs:
        raise SystemExit(f"no eval cells found under: {', '.join(map(str, paths))}")
    return run_dirs


def arm_name(run_dir: Path) -> str:
    """'<game>__<moral_value>_<jobid>' -> '<moral_value>_<jobid>'.

    The job id stays in the label so every column/row group identifies
    its exact run — duplicate arms (e.g. seed replicates) can never
    shadow each other."""
    name = run_dir.name
    if "__" in name:
        name = name.split("__", 1)[1]
    return name


def load_json(run_dir: Path, filename: str) -> Optional[Dict]:
    path = run_dir / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def print_state_table(arm_cells: Dict[str, Dict[str, str]]) -> None:
    """states as rows, arms as columns. arm_cells: arm -> state -> cell."""
    width = max(
        [len(a) for a in arm_cells]
        + [len(c) for cells in arm_cells.values() for c in cells.values()]
    )
    print("state | " + " | ".join(a.ljust(width) for a in arm_cells))
    for state in STATES:
        row = [arm_cells[a][state].ljust(width) for a in arm_cells]
        print(f"{state:5s} | " + " | ".join(row))


# ------------------------------------------------------------ behavioral

def summarize_behavioral(run_dirs: List[Path]) -> None:
    """Rollout eval: what the model DOES, per opponent."""
    print("\n=== behavioral (rollout episodes) ===")
    for run_dir in run_dirs:
        data = load_json(run_dir, "behavioral.json")
        if data is None:
            continue
        meta = data["metadata"]
        # Protocol identity: 'custom' just means the run was launched via
        # explicit flags, so print the resolved settings that actually
        # define the stage (1 round+hist = single_round; 5 rounds+nohist =
        # multi_round, +conversation = multi_round_conversation).
        print(f"\n{arm_name(run_dir)}  "
              f"(episodes/opp={meta['num_episodes']}, T={meta['eval_temperature']}, "
              f"protocol={meta['protocol']}: {meta['num_rounds']}rd "
              f"{meta['game_design']}"
              f"{' conversation' if meta.get('conversation') else ''})")
        for block in data["opponents"]:
            cond_c, cond_d = block["cond_given_opp_c"], block["cond_given_opp_d"]
            coop = block["cooperation_rate"]
            coop_str = f"{coop:5.0%}" if coop is not None else "  n/a"
            print(f"  vs {block['opponent']:16s}"
                  f" coop {coop_str}"
                  f"  P(C|oppC) {cond_c['p_C']:5.0%} (n={cond_c['n']:3d})"
                  f"  P(C|oppD) {cond_d['p_C']:5.0%} (n={cond_d['n']:3d})"
                  f"  parse_fail {block['parse_failure_rate']:.0%}")


# --------------------------------------------------------------- probe A

def summarize_probe_a(run_dirs: List[Path]) -> None:
    """Deterministic answer-token probe: label log-odds shift per state."""
    print("\n=== probe A (answer-token log-odds; delta = teacher - student) ===")
    arm_cells: Dict[str, Dict[str, str]] = {}
    for run_dir in run_dirs:
        data = load_json(run_dir, "probe_a.json")
        if data is None:
            continue
        arm_cells[arm_name(run_dir)] = {
            state: (f"{r['answer_delta']:+.2f}"
                    f" pC {r['p_coop_student']:.2f}->{r['p_coop_teacher']:.2f}")
            for state, r in data["probe_a"].items()
        }
    if arm_cells:
        print_state_table(arm_cells)
    else:
        print("(no probe_a.json in any cell)")


# --------------------------------------------------------------- probe B

def _mode_splits(run_dir: Path) -> Dict[str, List[int]]:
    """Per state: [C-ward, D-ward] counts of answer_delta over traces."""
    splits = {state: [0, 0] for state in STATES}
    with open(run_dir / "probe_b.traces.jsonl") as f:
        for line in f:
            rec = json.loads(line)
            delta = rec.get("answer_delta")
            if delta is not None:
                splits[rec["state"]][0 if delta > 0 else 1] += 1
    return splits


def summarize_probe_b(run_dirs: List[Path]) -> None:
    """Trace probe: one table per metric; answer_delta with mode split."""
    arms = {
        arm_name(d): (data["probe_b"], _mode_splits(d))
        for d in run_dirs
        if (data := load_json(d, "probe_b.json")) is not None
    }
    if not arms:
        print("\n=== probe B ===\n(no probe_b.json in any cell)")
        return
    for metric in PROBE_B_METRICS:
        print(f"\n=== probe B: {metric} ===")
        arm_cells: Dict[str, Dict[str, str]] = {}
        for arm, (summary, splits) in arms.items():
            cells = {}
            for state in STATES:
                m = summary[state][metric]
                cell = f"{m['mean']:+.3f}±{m['std']:.3f}"
                if metric == "answer_delta":
                    cell += " ({}/{})".format(*splits[state])
                cells[state] = cell
            arm_cells[arm] = cells
        print_state_table(arm_cells)
    print("\nanswer_delta (a/b) = traces shifted C-ward / D-ward; "
          "n and parse-fail counts in each probe_b.json.")


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-arm summary tables for teacher-signal eval cells")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="Eval-group directory (scanned for cells) or "
                             "explicit run directories.")
    parser.add_argument("--section", choices=SECTIONS, default=None,
                        help="Print one section only (default: all).")
    parser.add_argument("--save", action="store_true",
                        help="Also write the tables to summary.txt in the "
                             "first given path (inside eval_results/, which "
                             "stays out of git).")
    args = parser.parse_args()

    run_dirs = discover_run_dirs(args.paths)
    summarizers: Dict[str, Callable[[List[Path]], None]] = {
        "behavioral": summarize_behavioral,
        "probe_a": summarize_probe_a,
        "probe_b": summarize_probe_b,
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for section in ([args.section] if args.section else SECTIONS):
            summarizers[section](run_dirs)
    print(buf.getvalue(), end="")

    if args.save:
        out_path = args.paths[0] / "summary.txt"
        out_path.write_text(buf.getvalue())
        print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
