#!/usr/bin/env python3.11
"""Submit an eval sweep: one sbatch job per cell of the declared grid.

Usage (login node, from the repo root):
    /usr/bin/python3.11 scripts/slurm/submit_sweep.py configs/sweeps/<name>.yaml
    ... --dry-run     # print the expansion without submitting

Writes eval_results/<results_dir>/<eval_group>/sweep_manifest.json
(results_dir: teacher_signal for base-model screens, post_training for
checkpoint evals):
the sweep spec, submission timestamp, git commit, and the job id + run
dir of every cell — the experiment's own record of what was launched.
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
    MANIFEST_NAME, PACK_LAUNCHER, PACK_SIZE, batch_payload, cell_submission,
    expand_cells, load_sweep, pack_batches, results_dir, run_dir_stem,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="sweep YAML (configs/sweeps/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the expansion and commands; submit nothing")
    parser.add_argument("--no-pack", action="store_true",
                        help="one job per cell (the old one-cell-per-node "
                             "path). Costs ~4x the billed node-hours, since "
                             "Clariden allocates whole 4-GPU nodes; use only "
                             "to isolate a packing problem.")
    parser.add_argument("--pack-size", type=int, default=PACK_SIZE,
                        help=f"cells per node (default {PACK_SIZE} = one per "
                             f"GPU)")
    args = parser.parse_args()

    spec = load_sweep(str(args.spec))
    cells = expand_cells(spec)
    group_dir = REPO_ROOT / "eval_results" / results_dir(spec) / spec["eval_group"]

    if args.no_pack:
        return _submit_unpacked(spec, cells, group_dir, args.dry_run)

    batches = pack_batches(spec, cells, args.pack_size)
    print(f"sweep {spec['name']}: {len(cells)} cells in {len(batches)} packed "
          f"jobs ({args.pack_size}/node) -> {group_dir}")
    # Per-node work orders, not results: kept out of the group root so it
    # holds only cells/, analysis/ and the manifest. One file per packed
    # sbatch job = the 4 cells that share a node.
    batch_dir = group_dir / "packed_node_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for b_idx, batch in enumerate(batches):
        payload = batch_payload(spec, batch)
        batch_path = batch_dir / f"batch_{b_idx:03d}.json"
        desc = [" ".join(f"{k}={v}" for k, v in c.items()) for c in batch]
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
        for cell, d in zip(batch, desc):
            # run_dir mirrors what the launcher builds: stem + _<jobid>.
            records.append({**cell, "job_id": job_id,
                            "run_dir": f"{run_dir_stem(cell)}_{job_id}",
                            "batch": b_idx,
                            "batch_file": batch_path.name})
            print(f"          {d}")

    if args.dry_run:
        return
    _write_manifest(spec, records, group_dir, packed=not args.no_pack)


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


def _write_manifest(spec, records, group_dir, packed: bool) -> None:
    group_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "sweep": spec,
        "submitted": datetime.now().isoformat(),
        "git_commit": git_provenance(),
        "packed": packed,
        "cells": records,
    }
    manifest_path = group_dir / MANIFEST_NAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
