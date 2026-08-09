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
SECTIONS = ("behavioral", "probe_a", "probe_b", "slices")


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


DEFAULT_AXES_KEYS = {"game_type", "moral_value", "representation", "protocol"}
# Metadata that legitimately differs per run (provenance, not settings).
PER_RUN_KEYS = {"timestamp", "slurm_job_id", "run_name", "experiment_name",
                "checkpoint", "training_seed", "config", "git_commit"}
# Set by the protocol preset itself — vary WITH protocol, not besides it.
PROTOCOL_DERIVED = {"num_rounds", "game_design", "num_episodes"}


def check_comparability(run_dirs: List[Path]) -> bool:
    """Warn when cells differ in any setting that is not a comparison axis.

    Axes come from the group's sweep_manifest.json when present (mapped
    to metadata keys), else the standard four. Differences there are the
    experiment; differences anywhere else mean the cells are NOT
    comparable on the axes alone. Returns True when clean.
    """
    axis_keys = set(DEFAULT_AXES_KEYS)
    manifest = run_dirs[0].parent / "sweep_manifest.json"
    if manifest.exists():
        with open(manifest) as f:
            declared = json.load(f)["sweep"]["axes"]
        rename = {"game": "game_type", "temperature": "eval_temperature"}
        # Presentation axes (--eval-labels etc.) all land in the
        # eval_presentation metadata dict — declaring any of them as a
        # sweep axis declares that dict as varying.
        rename.update({f"eval_{ax}": "eval_presentation"
                       for ax in ("labels", "layout", "label_order",
                                  "role", "payoffs")})
        axis_keys = {rename.get(a, a) for a in declared}
    excluded = PER_RUN_KEYS | axis_keys
    if "protocol" in axis_keys:
        excluded |= PROTOCOL_DERIVED

    by_key: Dict[str, Dict[str, List[str]]] = {}
    for run_dir in run_dirs:
        meta = (load_json(run_dir, "behavioral.json") or {}).get("metadata", {})
        for key, value in meta.items():
            if key not in excluded:
                by_key.setdefault(key, {}).setdefault(
                    json.dumps(value, sort_keys=True), []).append(run_dir.name)

    drift = {k: v for k, v in by_key.items() if len(v) > 1}
    for key, by_value in sorted(drift.items()):
        print(f"WARNING undeclared variation in '{key}':")
        for value, cells in by_value.items():
            print(f"    {value}: {', '.join(cells)}")
    if not drift:
        print(f"comparability: OK (axes {sorted(axis_keys)}; "
              f"{len(by_key)} other settings constant)")
    return not drift


def arm_name(run_dir: Path) -> str:
    """'<game>__<moral_value>_<jobid>' -> '<moral_value>_<jobid>' (fallback
    label when metadata is unavailable or nothing varies)."""
    name = run_dir.name
    if "__" in name:
        name = name.split("__", 1)[1]
    return name


_AXIS_FIELDS = ("game_type", "moral_value", "representation", "protocol")
_PRESENTATION_AXES = ("labels", "layout", "label_order", "role", "payoffs")


def _label_parts(meta: Dict) -> List[str]:
    """Candidate label components: the four axis fields plus the five
    presentation settings (rendered 'axis=value' since bare values like
    'randomize' would be ambiguous across axes)."""
    parts = [str(meta.get(f)) for f in _AXIS_FIELDS]
    presentation = meta.get("eval_presentation") or {}
    parts += [f"{ax}={presentation.get(ax, 'fixed')}"
              for ax in _PRESENTATION_AXES]
    return parts


def arm_labels(run_dirs: List[Path]) -> Dict[Path, str]:
    """Column/row-group label per cell, built from the axis fields that
    actually VARY across the group (so a matrix-vs-prose sweep labels
    arms 'deontological|matrix' / 'deontological|prose', not two job
    ids). Job id is appended only when needed to keep labels unique
    (e.g. seed replicates)."""
    values = {
        d: _label_parts((load_json(d, "behavioral.json") or {})
                        .get("metadata", {}))
        for d in run_dirs
    }
    n_parts = len(next(iter(values.values()))) if values else 0
    varying = [i for i in range(n_parts)
               if len({vals[i] for vals in values.values()}) > 1]
    labels = {d: "|".join(vals[i] for i in varying) if varying
              else arm_name(d)
              for d, vals in values.items()}
    counts: Dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    return {d: (f"{label}|{d.name.rsplit('_', 1)[-1]}"
                if counts[label] > 1 else label)
            for d, label in labels.items()}


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

def summarize_behavioral(run_dirs: List[Path], labels: Dict[Path, str]) -> None:
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
        # multi_round, which is always a conversation).
        print(f"\n{labels[run_dir]}  "
              f"(episodes/opp={meta['num_episodes']}, T={meta['eval_temperature']}, "
              f"protocol={meta['protocol']}: {meta['num_rounds']}rd "
              f"{meta['game_design']}"
              f"{' restate_rules' if meta.get('restate_rules_per_round') else ''})")
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

def summarize_probe_a(run_dirs: List[Path], labels: Dict[Path, str]) -> None:
    """Deterministic answer-token probe: label log-odds shift per state."""
    print("\n=== probe A (answer-token log-odds; delta = teacher - student) ===")
    arm_cells: Dict[str, Dict[str, str]] = {}
    for run_dir in run_dirs:
        data = load_json(run_dir, "probe_a.json")
        if data is None:
            continue
        arm_cells[labels[run_dir]] = {
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


def summarize_probe_b(run_dirs: List[Path], labels: Dict[Path, str]) -> None:
    """Trace probe: one table per metric; answer_delta with mode split."""
    arms = {
        labels[d]: (data["probe_b"], _mode_splits(d))
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


# ----------------------------------------------------------------- slices

def _facet_extractors(presentations: List[Dict]) -> Dict[str, Callable]:
    """Facet -> level extractor over one cell's episode presentations.
    Only facets with >1 observed level are returned, so the section
    automatically shows exactly the axes that were randomized."""
    def greed(p):
        return p["payoffs"]["T"] - p["payoffs"]["R"]

    def fear(p):
        return p["payoffs"]["P"] - p["payoffs"]["S"]

    greed_med = sorted(greed(p) for p in presentations)[len(presentations) // 2]
    fear_med = sorted(fear(p) for p in presentations)[len(presentations) // 2]
    facets = {
        "labels": lambda p: ("coop-alphabetically-first"
                             if p["coop_label"] < p["defect_label"]
                             else "defect-alphabetically-first"),
        "layout": lambda p: f"layout={p['matrix_layout']}",
        "role": lambda p: "row" if p["agent_is_row"] else "column",
        "opener_order": lambda p: ("coop-first"
                                   if p["opener_order"][0] == p["coop_label"]
                                   else "defect-first"),
        "closer_order": lambda p: ("coop-first"
                                   if p["closer_order"][0] == p["coop_label"]
                                   else "defect-first"),
        "greed T-R": lambda p, m=greed_med: f"T-R{'>' if greed(p) > m else '<='}{m}",
        "fear P-S": lambda p, m=fear_med: f"P-S{'>' if fear(p) > m else '<='}{m}",
    }
    return {name: fn for name, fn in facets.items()
            if len({fn(p) for p in presentations}) > 1}


def _episode_decisions(ep: Dict, ep_idx: int, meta: Dict) -> List[Dict]:
    """(move, opp_prev) per decision of one episode. opp_prev comes from
    the within-episode transition; for balanced single-round hist runs
    the fabricated prev is reconstructed from the deterministic cycle
    (trajectory.FAB_STATES, episode i -> i % 4)."""
    agent, opp = ep["agent"], ep["opp"]
    decisions = []
    single_round_fab = (meta.get("game_design") == "hist"
                        and meta.get("num_rounds") == 1
                        and meta.get("state_design", "balanced") == "balanced")
    for t, move in enumerate(agent):
        if t > 0:
            opp_prev = opp[t - 1] if opp[t - 1] in ("C", "D") else None
        elif single_round_fab:
            opp_prev = ("C", "D", "C", "D")[ep_idx % 4]
        else:
            opp_prev = None
        decisions.append({"move": move, "opp_prev": opp_prev})
    return decisions


def summarize_slices(run_dirs: List[Path], labels: Dict[Path, str]) -> None:
    """Within-run robustness: slice a randomized-presentation cell's
    episodes by facet and compare P(C) / conditionals across levels.
    Episodes are pooled over opponents (randomization balances opponents
    within each level)."""
    print("\n=== slices (within-run, by presentation facet) ===")
    any_cell = False
    for run_dir in run_dirs:
        data = load_json(run_dir, "behavioral.json")
        if data is None:
            continue
        meta = data["metadata"]
        episodes = [(ep, block) for block in data["opponents"]
                    for ep in block.get("episode_moves", [])
                    if "presentation" in ep]
        if not episodes:
            continue
        any_cell = True
        facets = _facet_extractors([ep["presentation"] for ep, _ in episodes])
        print(f"\n{labels[run_dir]}  (pooled over "
              f"{len(data['opponents'])} opponents, {len(episodes)} episodes)")
        print("| facet | level | eps | illegal | P(C) | P(C|oppC) | P(C|oppD) | gap |")
        print("|---|---|---|---|---|---|---|---|")
        for facet, fn in facets.items():
            by_level: Dict[str, List] = {}
            for i, (ep, _) in enumerate(episodes):
                by_level.setdefault(fn(ep["presentation"]), []).append(
                    _episode_decisions(ep, i, meta))
            for level in sorted(by_level):
                decs = [d for ep_decs in by_level[level] for d in ep_decs]
                legal = [d for d in decs if d["move"] in ("C", "D")]

                def p_c(subset):
                    return (f"{sum(d['move'] == 'C' for d in subset) / len(subset):.0%}"
                            if subset else "—")

                cond_c = [d for d in legal if d["opp_prev"] == "C"]
                cond_d = [d for d in legal if d["opp_prev"] == "D"]
                gap = (f"{(sum(d['move'] == 'C' for d in cond_c) / len(cond_c)) - (sum(d['move'] == 'C' for d in cond_d) / len(cond_d)):+.0%}"
                       if cond_c and cond_d else "—")
                print(f"| {facet} | {level} | {len(by_level[level])} "
                      f"| {1 - len(legal) / len(decs):.0%} | {p_c(legal)} "
                      f"| {p_c(cond_c)} | {p_c(cond_d)} | {gap} |")
    if not any_cell:
        print("(no randomized-presentation cells — per-episode presentation "
              "is only recorded when an axis is randomized)")


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
    check_comparability(run_dirs)
    labels = arm_labels(run_dirs)
    summarizers: Dict[str, Callable[..., None]] = {
        "behavioral": summarize_behavioral,
        "probe_a": summarize_probe_a,
        "probe_b": summarize_probe_b,
        "slices": summarize_slices,
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for section in ([args.section] if args.section else SECTIONS):
            summarizers[section](run_dirs, labels)
    print(buf.getvalue(), end="")

    if args.save:
        out_path = args.paths[0] / "summary.txt"
        out_path.write_text(buf.getvalue())
        print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
