#!/usr/bin/env python3.11
"""Submit an eval sweep: the declared grid, packed 4 cells per node.

Usage (login node, from the repo root):
    /usr/bin/python3.11 scripts/slurm/submit_sweep.py \
        configs/eval/<teacher_signal|post_training|transfer>/<subject>/<family>/<experiment>.yaml
    ... --dry-run     # print the expansion without submitting
    ... specA.yaml specB.yaml   # several specs: their cells share nodes

The spec's path is its identity (docs/naming.md): results land at the
mirrored path eval_results/<results_root>/<subject>/<experiment>/, where
sweep_manifest.json records the sweep spec, submission timestamp, git
commit, and the job id + run dir of every cell — the experiment's own
record of what was launched.

Several specs at once pool their cells into shared nodes (a spec cannot
span game families, a node can): two 2-cell specs become one packed job
instead of two half-empty nodes. Each spec keeps its own results dir and
manifest; the manifest names the co-packed specs and where the batch
payloads live (under the FIRST spec's group dir, whose name the pack log
also carries).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from moralgym_verl.eval.config import git_provenance          # noqa: E402
from moralgym_verl.eval.sweep import (                        # noqa: E402
    MANIFEST_NAME, PACK_LAUNCHER, PACK_SIZE, batch_payload_items,
    cell_submission, expand_cells, load_sweep, pack_items, results_dir,
    run_dir_stem,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs", type=Path, nargs="+", metavar="spec",
                        help="sweep YAML(s) (configs/eval/<root>/<subject>/"
                             "<family>/); several share nodes")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the expansion and commands; submit nothing")
    parser.add_argument("--no-pack", action="store_true",
                        help="one job per cell (the old one-cell-per-node "
                             "path). Costs ~4x the billed node-hours, since "
                             "Clariden allocates whole 4-GPU nodes; use only "
                             "to isolate a packing problem. Single spec only.")
    parser.add_argument("--pack-size", type=int, default=PACK_SIZE,
                        help=f"cells per node (default {PACK_SIZE} = one per "
                             f"GPU)")
    args = parser.parse_args()

    specs, group_dirs, items = [], [], []
    for path in args.specs:
        spec = load_sweep(str(path))
        spec["sweep_path"] = str(path)
        if not (REPO_ROOT / spec["config"]).exists():
            sys.exit(f"eval config not found: {spec['config']} (harness "
                     f"profiles: configs/eval/harness/<model>/<family>.yaml)")
        specs.append(spec)
        group_dirs.append(REPO_ROOT / "eval_results" / results_dir(spec)
                          / spec["eval_group"])
        items += [(spec, cell) for cell in expand_cells(spec)]
    if len({s["eval_group"] for s in specs}) != len(specs):
        sys.exit("the same spec was given twice")

    if args.no_pack:
        if len(specs) != 1:
            sys.exit("--no-pack takes a single spec")
        cells = [cell for _, cell in items]
        return _submit_unpacked(specs[0], cells, group_dirs[0], args.dry_run)

    batches = pack_items(items, args.pack_size)
    for spec, gdir in zip(specs, group_dirs):
        n = sum(1 for s, _ in items if s is spec)
        print(f"sweep {spec['name']}: {n} cells -> {gdir}")
    print(f"{len(items)} cells in {len(batches)} packed jobs "
          f"({args.pack_size}/node)"
          + (f", {len(specs)} specs sharing nodes" if len(specs) > 1 else ""))
    # Per-node work orders, not results: kept out of the group root so it
    # holds only cells/, analysis/ and the manifest. One file per packed
    # sbatch job = the cells that share a node. With several specs the
    # payloads live under the FIRST spec's group dir; every manifest says so.
    batch_dir = group_dirs[0] / "packed_node_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    records = {id(spec): [] for spec in specs}
    for b_idx, batch in enumerate(batches):
        payload = batch_payload_items(batch)
        batch_path = batch_dir / f"batch_{b_idx:03d}.json"
        desc = [f"[{spec['name']}] "
                + " ".join(f"{k}={v}" for k, v in cell.items())
                for spec, cell in batch]
        if args.dry_run:
            print(f"  [dry] sbatch {PACK_LAUNCHER} {batch_path.name}"
                  f"  ({len(batch)} cells)")
            for d in desc:
                print(f"          {d}")
            continue
        with open(batch_path, "w") as f:
            json.dump(payload, f, indent=2)
        result = subprocess.run(
            ["sbatch", PACK_LAUNCHER, str(batch_path)],
            cwd=REPO_ROOT, env=os.environ, capture_output=True, text=True,
        )
        match = re.search(r"Submitted batch job (\d+)", result.stdout)
        if result.returncode != 0 or not match:
            sys.exit(f"sbatch failed for batch {b_idx}:\n"
                     f"{result.stdout}{result.stderr}")
        job_id = match.group(1)
        print(f"  {job_id}  batch {b_idx} ({len(batch)} cells)")
        for (spec, cell), d in zip(batch, desc):
            # run_dir mirrors what the launcher builds: stem + _<jobid>.
            records[id(spec)].append({**cell, "job_id": job_id,
                                      "run_dir": f"{run_dir_stem(cell)}_{job_id}",
                                      "batch": b_idx,
                                      "batch_file": batch_path.name})
            print(f"          {d}")

    if args.dry_run:
        return
    for spec, gdir in zip(specs, group_dirs):
        co_packed = ([s["name"] for s in specs if s is not spec]
                     if len(specs) > 1 else None)
        _write_manifest(spec, records[id(spec)], gdir, packed=True,
                        batch_dir=batch_dir, co_packed=co_packed)


def _submit_unpacked(spec, cells, group_dir, dry_run: bool) -> None:
    """One job per cell — the pre-packing path, kept for isolating problems."""
    print(f"sweep {spec['name']}: {len(cells)} cells, UNPACKED "
          f"(1 node each) -> {group_dir}")
    records = []
    for cell in cells:
        env_overrides, argv = cell_submission(spec, cell)
        cell_desc = " ".join(f"{k}={v}" for k, v in cell.items())
        if dry_run:
            env_desc = " ".join(f"{k}={v}" for k, v in env_overrides.items())
            print(f"  [dry] {env_desc} {' '.join(argv)}")
            continue
        result = subprocess.run(
            argv, cwd=REPO_ROOT, env={**os.environ, **env_overrides},
            capture_output=True, text=True,
        )
        match = re.search(r"Submitted batch job (\d+)", result.stdout)
        if result.returncode != 0 or not match:
            sys.exit(f"sbatch failed for cell ({cell_desc}):\n"
                     f"{result.stdout}{result.stderr}")
        job_id = match.group(1)
        records.append({**cell, "job_id": job_id,
                        "run_dir": f"{cell['game']}__{cell['moral_value']}_{job_id}",
                        "env": env_overrides})
        print(f"  {job_id}  {cell_desc}")
    if dry_run:
        return
    _write_manifest(spec, records, group_dir, packed=False)


def _write_manifest(spec, records, group_dir, packed: bool,
                    batch_dir: Path | None = None,
                    co_packed: list | None = None) -> None:
    group_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "sweep": spec,
        "submitted": datetime.now().isoformat(),
        "git_commit": git_provenance(),
        "packed": packed,
        "cells": records,
    }
    if batch_dir is not None and batch_dir.parent != group_dir:
        # Co-packed submission: the batch payloads (and the pack log's
        # name) belong to the first spec given on the command line.
        manifest["batch_dir"] = str(batch_dir.relative_to(REPO_ROOT))
    if co_packed:
        manifest["co_packed_with"] = co_packed
    manifest_path = group_dir / MANIFEST_NAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
