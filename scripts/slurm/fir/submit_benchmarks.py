#!/usr/bin/env python3
"""FIR VARIANT of scripts/slurm/submit_benchmarks.py (the Clariden original
is untouched): submits scripts/slurm/fir/benchmark_pack.sh with Fir
resource flags (scripts/slurm/fir/fir.env). Keep in step with the original.

Submit a benchmark sweep: one cell per checkpoint, packed 4 per node.

Usage (login node, from the repo root, with the venv python):
    ~/venvs/moralgym/bin/python scripts/slurm/fir/submit_benchmarks.py \
        configs/eval/benchmarks/<model>/<family>/<experiment>.yaml [more...]
    ... --dry-run     # print the expansion without submitting

The spec's path is its identity (docs/naming.md): results land at
eval_results/benchmarks/<model>/<family>/<experiment>/, with
sweep_manifest.json recording the spec, submission time, git commit and
the job id + run dir of every cell. Several specs share nodes; each keeps
its own results dir and manifest, payloads live under the first spec's.
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

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fir_env import load_fir_env, sbatch_resource_args        # noqa: E402
from moralgym_verl.eval.benchmarks import (                  # noqa: E402
    MANIFEST_NAME, batch_payload, expand_cells, load_spec,
    pack_batches, run_dir_stem,
)

PACK_LAUNCHER = "scripts/slurm/fir/benchmark_pack.sh"   # the Fir launcher
from moralgym_verl.eval.config import git_provenance          # noqa: E402
from moralgym_verl.eval.sweep import PACK_SIZE                # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs", type=Path, nargs="+", metavar="spec")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the expansion and commands; submit nothing")
    parser.add_argument("--pack-size", type=int, default=PACK_SIZE,
                        help=f"cells per node (default {PACK_SIZE})")
    args = parser.parse_args()

    specs, items = [], []
    for path in args.specs:
        spec = load_spec(str(path))
        spec["sweep_path"] = str(path)
        specs.append(spec)
        items += [(spec, cell) for cell in expand_cells(spec)]
    if len({s["eval_group"] for s in specs}) != len(specs):
        sys.exit("the same spec was given twice")
    group_dirs = [REPO_ROOT / "eval_results" / s["results_dir"] / s["eval_group"]
                  for s in specs]

    batches = pack_batches(items, args.pack_size)
    for spec in specs:
        n = sum(1 for s, _ in items if s is spec)
        print(f"sweep {spec['name']}: {n} cells x {spec['benchmarks']}")
    print(f"{len(items)} cells in {len(batches)} packed jobs ({args.pack_size}/node)")

    batch_dir = group_dirs[0] / "packed_node_batches"
    records = {id(spec): [] for spec in specs}
    for b_idx, batch in enumerate(batches):
        payload = {"cells": []}
        for spec, cell in batch:
            payload["cells"] += batch_payload(spec, [cell])["cells"]
        batch_path = batch_dir / f"batch_{b_idx:03d}.json"
        desc = [f"[{spec['name']}] checkpoint={cell['checkpoint']}"
                for spec, cell in batch]
        sbatch_cmd = ["sbatch", *sbatch_resource_args("node"),
                      PACK_LAUNCHER, str(batch_path)]
        if args.dry_run:
            print(f"  [dry] {' '.join(sbatch_cmd[:-1])} {batch_path.name}  ({len(batch)} cells)")
            for d in desc:
                print(f"          {d}")
            continue
        batch_dir.mkdir(parents=True, exist_ok=True)
        with open(batch_path, "w") as f:
            json.dump(payload, f, indent=2)
        result = subprocess.run(sbatch_cmd, cwd=REPO_ROOT, env=os.environ,
                                capture_output=True, text=True)
        match = re.search(r"Submitted batch job (\d+)", result.stdout)
        if result.returncode != 0 or not match:
            sys.exit(f"sbatch failed for batch {b_idx}:\n{result.stdout}{result.stderr}")
        job_id = match.group(1)
        print(f"  {job_id}  batch {b_idx} ({len(batch)} cells)")
        for (spec, cell), d in zip(batch, desc):
            records[id(spec)].append({**cell, "job_id": job_id,
                                      "run_dir": f"{run_dir_stem(cell)}_{job_id}",
                                      "batch": b_idx, "batch_file": batch_path.name})
            print(f"          {d}")
    if args.dry_run:
        return

    for spec, gdir in zip(specs, group_dirs):
        gdir.mkdir(parents=True, exist_ok=True)
        manifest = {"sweep": spec, "submitted": datetime.now().isoformat(),
                    "git_commit": git_provenance(), "packed": True,
                    "cells": records[id(spec)]}
        if batch_dir.parent != gdir:
            manifest["batch_dir"] = str(batch_dir.relative_to(REPO_ROOT))
        if len(specs) > 1:
            manifest["co_packed_with"] = [s["name"] for s in specs if s is not spec]
        with open(gdir / MANIFEST_NAME, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"manifest: {gdir / MANIFEST_NAME}")


if __name__ == "__main__":
    # scripts/slurm/cluster.env + scripts/slurm/fir/fir.env into the
    # environment (fir_env.py); the launchers re-read both inside the job.
    load_fir_env()
    main()
