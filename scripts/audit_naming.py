#!/usr/bin/env python3.11
"""Audit the naming contract (docs/naming.md): configs/eval must mirror
eval_results, and every post_training subject should be a known training run.

    /usr/bin/python3.11 scripts/audit_naming.py

Exit 0 = clean (informational lines allowed), 1 = orphans found. Underscore
dirs and files (_harness.yaml, _debug, _archive) are outside the contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SWEEPS = REPO / "configs" / "eval"
RESULTS = REPO / "eval_results"
TRAINING = REPO / "configs" / "training"
ROOTS = ("teacher_signal", "post_training")


def is_flat_group(d: Path) -> bool:
    """A pre-contract results dir: cells live directly under it instead of
    under a <subject>/<experiment>/ pair."""
    return ((d / "cells").is_dir() or (d / "sweep_manifest.json").exists()
            or (d / "packed_node_batches").is_dir())


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from moralgym_verl.eval.sweep import GAME_FAMILIES

    specs, results, legacy = set(), set(), []
    problems = 0
    for root in ROOTS:
        for spec in sorted((SWEEPS / root).glob("[!_]*/[!_]*/[!_]*.yaml")):
            specs.add((root, spec.parent.parent.name, spec.parent.name,
                       spec.stem))
        for subject in sorted((RESULTS / root).glob("[!_]*")):
            if not subject.is_dir():
                continue
            if is_flat_group(subject):
                legacy.append(f"{root}/{subject.name}")
                continue
            for family in sorted(subject.glob("[!_]*")):
                if not family.is_dir():
                    continue
                if family.name not in GAME_FAMILIES:
                    print(f"UNKNOWN FAMILY: eval_results/{root}/"
                          f"{subject.name}/{family.name} is not one of "
                          f"{sorted(GAME_FAMILIES)}")
                    problems += 1
                    continue
                for exp in sorted(family.glob("[!_]*")):
                    if exp.is_dir():
                        results.add((root, subject.name, family.name,
                                     exp.name))

    for root, subject, family, exp in sorted(specs - results):
        print(f"NO RESULTS yet for configs/eval/{root}/{subject}/{family}/"
              f"{exp}.yaml (fine if not yet run)")
    for root, subject, family, exp in sorted(results - specs):
        print(f"ORPHAN results: eval_results/{root}/{subject}/{family}/{exp} "
              f"has no sweep yaml — backfill "
              f"configs/eval/{root}/{subject}/{family}/{exp}.yaml")
        problems += 1

    run_configs = {p.stem for p in TRAINING.glob("[!_]*.yaml")}
    for root, subject, family, exp in sorted(specs):
        if (root == "post_training" and not subject.startswith("cross_")
                and subject not in run_configs):
            print(f"UNKNOWN RUN: post_training subject {subject!r} has no "
                  f"configs/training/{subject}.yaml")
            problems += 1

    print(f"\n{len(specs)} experiments declared, {len(results)} result dirs, "
          f"{problems} problem(s)")
    if legacy:
        print(f"note: pre-contract dirs pending migration: {legacy}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
