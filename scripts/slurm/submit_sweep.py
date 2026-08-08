#!/usr/bin/env python3.11
"""Submit an eval sweep: one sbatch job per cell of the declared grid.

Usage (login node, from the repo root):
    /usr/bin/python3.11 scripts/slurm/submit_sweep.py configs/sweeps/<name>.yaml
    ... --dry-run     # print the expansion without submitting

Writes eval_results/teacher_signal/<eval_group>/sweep_manifest.json:
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
    MANIFEST_NAME, cell_submission, expand_cells, load_sweep,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="sweep YAML (configs/sweeps/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the expansion and commands; submit nothing")
    args = parser.parse_args()

    spec = load_sweep(str(args.spec))
    cells = expand_cells(spec)
    group_dir = REPO_ROOT / "eval_results" / "teacher_signal" / spec["eval_group"]

    print(f"sweep {spec['name']}: {len(cells)} cells -> {group_dir}")
    records = []
    for cell in cells:
        env_overrides, argv = cell_submission(spec, cell)
        cell_desc = " ".join(f"{k}={v}" for k, v in cell.items())
        if args.dry_run:
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
        run_dir = f"{cell['game']}__{cell['moral_value']}_{job_id}"
        records.append({**cell, "job_id": job_id, "run_dir": run_dir,
                        "env": env_overrides})
        print(f"  {job_id}  {cell_desc}")

    if args.dry_run:
        return
    group_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "sweep": spec,
        "submitted": datetime.now().isoformat(),
        "git_commit": git_provenance(),
        "cells": records,
    }
    manifest_path = group_dir / MANIFEST_NAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
