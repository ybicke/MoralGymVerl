"""Shared readers over teacher-signal eval cells.

A library, not a CLI: locating cells, loading their JSON, and the two
reconstructions that several analyses need (which presentation facets a
cell actually randomized, and the per-decision (move, opp_prev) stream
behind a behavioral run). Imported by publication_tables.py.

Stdlib only, so it runs on the login node without the container.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

STATES = ("first", "CC", "CD", "DC", "DD")


def discover_run_dirs(paths: List[Path]) -> List[Path]:
    """Explicit run dirs, or group dirs scanned for behavioral.json.

    Cells live in <group>/cells/; groups written before that layout keep
    them at the group root, so both are scanned and the results merged.
    """
    run_dirs: List[Path] = []
    for p in paths:
        if (p / "behavioral.json").exists():
            run_dirs.append(p)
        else:
            run_dirs.extend(sorted(
                d.parent for pattern in ("cells/*/behavioral.json",
                                         "*/behavioral.json")
                for d in p.glob(pattern)))
    if not run_dirs:
        raise SystemExit(f"no eval cells found under: {', '.join(map(str, paths))}")
    return run_dirs


def load_json(run_dir: Path, filename: str) -> Optional[Dict]:
    path = run_dir / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


DEFAULT_AXES_KEYS = {"game_type", "moral_value", "representation", "protocol",
                     "base_model"}
# Metadata that legitimately differs per run (provenance, not settings).
PER_RUN_KEYS = {"timestamp", "slurm_job_id", "run_name", "experiment_name",
                "checkpoint", "training_seed", "config", "git_commit"}
# Set by the protocol preset itself — vary WITH protocol, not besides it.
PROTOCOL_DERIVED = {"num_rounds", "game_design", "num_episodes"}
# Likewise: the --presentation spec is the intent, eval_presentation the
# resolved state. Declaring the axis declares both as varying together.
PRESENTATION_DERIVED = {"presentation_spec"}


def find_manifest(run_dir: Path) -> Optional[Path]:
    """The group manifest for a cell: <group>/sweep_manifest.json, whether
    the cell sits in <group>/cells/ or (older groups) at the group root."""
    for candidate in (run_dir.parent / "sweep_manifest.json",
                      run_dir.parent.parent / "sweep_manifest.json"):
        if candidate.exists():
            return candidate
    return None


def check_comparability(run_dirs: List[Path]) -> bool:
    """Warn when cells differ in any setting that is not a comparison axis.

    Axes come from the group's sweep_manifest.json when present (mapped
    to metadata keys), else the standard four. Differences there are the
    experiment; differences anywhere else mean the cells are NOT
    comparable on the axes alone. Returns True when clean.

    Call it per group: two groups can legitimately differ on an axis that
    neither declares (the screen is fixed-presentation, a robustness
    sweep randomized), and pooling them would report that as drift.
    """
    axis_keys = set(DEFAULT_AXES_KEYS)
    manifest = find_manifest(run_dirs[0])
    if manifest is not None:
        with open(manifest) as f:
            declared = json.load(f)["sweep"]["axes"]
        rename = {"game": "game_type", "temperature": "eval_temperature",
                  "model": "base_model",
                  # The --presentation spec resolves into the
                  # eval_presentation dict; declaring the axis declares
                  # that dict as varying (same as the eval_* axes below).
                  "presentation": "eval_presentation"}
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
    if "eval_presentation" in axis_keys:
        excluded |= PRESENTATION_DERIVED

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
    return not drift


def mode_splits(run_dir: Path) -> Dict[str, List[int]]:
    """Per state: [C-ward, D-ward] counts of answer_delta over traces."""
    splits = {state: [0, 0] for state in STATES}
    with open(run_dir / "probe_b.traces.jsonl") as f:
        for line in f:
            rec = json.loads(line)
            delta = rec.get("answer_delta")
            if delta is not None:
                splits[rec["state"]][0 if delta > 0 else 1] += 1
    return splits


def facet_extractors(presentations: List[Dict]) -> Dict[str, Callable]:
    """Facet -> level extractor over one cell's episode presentations.
    Only facets with >1 observed level are returned, so a caller
    automatically sees exactly the axes that were randomized."""
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


def episode_decisions(ep: Dict, ep_idx: int, meta: Dict) -> List[Dict]:
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
