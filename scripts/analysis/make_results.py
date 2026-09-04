#!/usr/bin/env python3.11
"""Build the results document(s) for an eval group.

One entrypoint for every kind of group. The kind is read from the group's
sweep manifest (falling back to the first cell's metadata):

    checkpoint axis + public_goods    -> specs/post_training_pgg  results_<experiment>.md (transfer)
    checkpoint axis present           -> specs/post_training   results_<experiment>.md
                                          + traces_checkpoints_*.md (+ traces_training_*.md with --rollouts)
    game == public_goods              -> specs/screen_pgg      results_pgg_<model>.md
    otherwise (2x2 games)             -> specs/screen_2x2      results_<model>.md + traces_<model>.md

Every document has the same skeleton (results_doc.py): header, moral value
prompts, prompt design (verbatim, from the traces), tables and figure,
example traces. Hand-written annotation goes in analysis_*.md beside it,
never in these files.

Login node, stdlib only:
    /usr/bin/python3.11 scripts/analysis/make_results.py eval_results/teacher_signal/<model>/<family>/<experiment>
    /usr/bin/python3.11 scripts/analysis/make_results.py eval_results/teacher_signal/<model>/classic/{screen,robustness,generosity_arm}
    /usr/bin/python3.11 scripts/analysis/make_results.py eval_results/teacher_signal/<model>/pgg/<experiment>/cells/*__list__*
    /usr/bin/python3.11 scripts/analysis/make_results.py eval_results/post_training/<run_name>/<family>/<experiment> \\
        --reference eval_results/teacher_signal/<screen> --reference eval_results/teacher_signal/<generosity_arm> \\
        --rollouts $SCRATCH/moralgym_verl_runs/<run>

Spec-specific options (--reference, --rollouts, --exemplars, ...) are
listed by --help once the kind is known; pass --kind to force one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_cells import discover_run_dirs, load_json  # noqa: E402

KINDS = ("post_training", "post_training_pgg", "screen_pgg", "screen_2x2")


def detect_kind(paths) -> str:
    """From the first path's manifest, else its first cell's metadata."""
    first = Path(paths[0])
    group = first if (first / "sweep_manifest.json").exists() else first.parent.parent
    manifest = group / "sweep_manifest.json"
    if manifest.exists():
        axes = json.loads(manifest.read_text()).get("sweep", {}).get("axes", {})
        if "checkpoint" in axes:
            return ("post_training_pgg" if axes.get("game") == ["public_goods"]
                    else "post_training")
        if axes.get("game") == ["public_goods"]:
            return "screen_pgg"
        if "game" in axes:
            return "screen_2x2"
    meta = (load_json(discover_run_dirs([first])[0], "behavioral.json") or {}).get("metadata", {})
    pgg = meta.get("game_type") == "public_goods"
    if (meta.get("checkpoint") or "base") != "base":
        return "post_training_pgg" if pgg else "post_training"
    return "screen_pgg" if pgg else "screen_2x2"


def main() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("paths", nargs="+", type=Path)
    pre.add_argument("--kind", choices=KINDS, default=None)
    known, _ = pre.parse_known_args()
    kind = known.kind or detect_kind(known.paths)

    import importlib
    spec = importlib.import_module(f"specs.{kind}")

    parser = argparse.ArgumentParser(
        description=f"{__doc__.splitlines()[0]}  [kind: {kind} -- {spec.build.__doc__.strip()}]")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="eval group directories or explicit cell directories")
    parser.add_argument("--kind", choices=KINDS, default=None,
                        help="force the document kind (default: from the manifest)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: <first path>/analysis)")
    parser.add_argument("--allow-mixed", action="store_true",
                        help="build even when a group's cells differ in an "
                             "undeclared setting (warn instead of refusing)")
    spec.add_arguments(parser)
    args = parser.parse_args()
    spec.build(args)


if __name__ == "__main__":
    main()
