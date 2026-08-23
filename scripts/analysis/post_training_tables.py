#!/usr/bin/env python3.11
"""Results document for a trained run's checkpoint eval.

Table P1 puts the trained checkpoints (none arm: no moral text in the
prompt) in the screen's own Table-1 frame -- fixed prose presentation,
same protocol, seed and episode count -- between the two reference rows
taken from the screen groups:

    base, no context          the pre-training policy
    trained @ step N ...      what training installed
    base + principle in context   the teacher the student distilled

Columns: P(C | state) for the four fabricated previous-round states,
pooled P(C) (mean of the four, balanced design) and D_opp, the screen's
opponent-conditioning gap P(C|C_O) - P(C|D_O). No spec-compliance flags:
targets belong to the interpretation (analysis_<run>.md), not the data.

Optional sections, same document: the training trajectory from the
run's rollout dumps (training_trajectory.py) and the reasoning-trace
statistics at the evaluated steps (trace_comparison.py).

Output: <group>/analysis/results_<group>.md (+ tex/), mirroring the
screen layout; the group is the experiment name
(<model>-<game>-<algo>-<principle>), the training run is recorded in
the header and the manifest. Login node, stdlib only:
    /usr/bin/python3.11 scripts/analysis/post_training_tables.py \
        eval_results/post_training/qwen3-8b-pd-sdpo-deon-repair-gen \
        --reference eval_results/teacher_signal/single_turn_screen_qwen3-8b \
        --reference eval_results/teacher_signal/generosity_arm_qwen3-8b \
        --principle deontological+repair+generosity \
        --rollouts ~/logs_verl/runs/qwen_run2_200
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from eval_cells import check_comparability, discover_run_dirs, load_json  # noqa: E402
from publication_tables import (  # noqa: E402
    D_OPP, STATES4, Cell, Table, behavioral, gap_pp, pct, plain,
    state_label, to_latex, to_markdown,
)
from training_trajectory import load_decisions, trajectory_table, windows  # noqa: E402
from trace_comparison import (  # noqa: E402
    from_cells, from_rollouts, principle_ngrams, stats, stats_table,
)
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402

CKPT_RE = re.compile(r"/([^/]+)/global_step_(\d+)/")


def checkpoint_of(meta: Dict) -> Tuple[str, int]:
    """('base', 0) for an untrained cell, else (run name, step)."""
    m = CKPT_RE.search(meta.get("checkpoint") or "base")
    return (m.group(1), int(m.group(2))) if m else ("base", 0)


def prose_fixed_pd(run_dir: Path) -> Optional[Dict]:
    meta = (load_json(run_dir, "behavioral.json") or {}).get("metadata")
    if not meta:
        return None
    pres = meta.get("eval_presentation") or {}
    if (meta["game_type"] != "prisoners_dilemma"
            or meta["representation"] != "prose"
            or any(v != "fixed" for v in pres.values())):
        return None
    return meta


def reference_rows(groups: List[Path], principle: str) -> Dict[str, Path]:
    """base-none and base+principle cells, first occurrence wins."""
    found: Dict[str, Path] = {}
    for run_dir in discover_run_dirs(groups):
        meta = prose_fixed_pd(run_dir)
        if meta is None or checkpoint_of(meta)[0] != "base":
            continue
        if meta["moral_value"] == "none":
            found.setdefault("base", run_dir)
        elif meta["moral_value"] == principle:
            found.setdefault("teacher", run_dir)
    return found


def trained_rows(group: Path) -> List[Tuple[int, Path]]:
    rows = []
    for run_dir in discover_run_dirs([group]):
        meta = prose_fixed_pd(run_dir)
        if meta is None:
            continue
        run, step = checkpoint_of(meta)
        if run != "base":
            rows.append((step, run_dir))
    return sorted(rows)


def row_cells(run_dir: Path) -> List[Cell]:
    block = behavioral(run_dir)
    sc = block["state_conditioning"]
    p = [sc[f"({s[0]},{s[1]})"]["p_C"] for s in STATES4]
    return ([Cell(pct(v)) for v in p]
            + [Cell(pct(sum(p) / len(p))), Cell(f"{gap_pp(block):+d}")])


def post_training_table(run: str, trained: List[Tuple[int, Path]],
                        refs: Dict[str, Path], principle: str) -> Table:
    meta = load_json(trained[0][1], "behavioral.json")["metadata"]
    model = meta["base_model"].rsplit("/", 1)[-1]
    rows = []
    if "base" in refs:
        rows.append(("base, no context", row_cells(refs["base"])))
    for step, run_dir in trained:
        rows.append((f"trained, step {step}", row_cells(run_dir)))
    if "teacher" in refs:
        rows.append((f"base + `{principle}` in context",
                     row_cells(refs["teacher"])))
    return Table(
        key=f"post-training-{run}",
        title=f"Table P1 — post-training: state-conditioned cooperation ({run})",
        caption=(
            f"Cooperation rate (\\%) of {model} by fabricated previous "
            "state, prose, fixed presentation, "
            f"$T={meta['eval_temperature']}$, {meta['num_episodes']} "
            "episodes per cell = 100 decisions per state (binomial s.e.\\ "
            "$\\leq$5 points; differences under $\\approx$14 points are "
            "not distinguishable). Trained rows carry NO moral text in "
            "the prompt. Pooled = mean of the four states. "
            f"{D_OPP} = P(C$\\mid$C$_O$) $-$ P(C$\\mid$D$_O$), the "
            "opponent-conditioning gap (reciprocity signature). Reference "
            "rows are the screen's own cells, same protocol."),
        stub="Policy",
        col_groups=[(None, [f"P(C$\\mid${state_label(s)})"]) for s in STATES4]
        + [(None, ["pooled"]), (None, [D_OPP])],
        panels=[(None, rows)],
    )


def header(run: str, group: Path, trained, refs: Dict[str, Path],
           ref_groups: List[Path]) -> str:
    meta = load_json(trained[0][1], "behavioral.json")["metadata"]
    steps = ", ".join(str(s) for s, _ in trained)
    ck = meta.get("checkpoint", "")
    return "\n".join([
        f"# Post-training eval: {group.name}",
        "",
        f"Training run `{run}`. ",
        f"{meta['base_model'].rsplit('/', 1)[-1]}, protocol "
        f"`{meta['protocol']}` (fabricated history, balanced states), prose, "
        f"fixed presentation, T = {meta['eval_temperature']}, "
        f"{meta['num_episodes']} episodes/cell, vs. random opponent. "
        f"Checkpoints: steps {steps} (LoRA adapters merged from "
        f"`{ck.rsplit('/global_step_', 1)[0]}`). Reference rows from: "
        + ", ".join(p.name for p in ref_groups) + ".",
        "",
        "States are the fabricated previous round, subscripted A (agent) "
        f"and O (opponent): {plain(state_label('CD'))} = agent cooperated, "
        "opponent defected; C = cooperate. Generated by "
        "`scripts/analysis/post_training_tables.py` (LaTeX in `tex/`).",
        "", "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("group", type=Path, help="checkpoint-eval group dir")
    parser.add_argument("--reference", action="append", type=Path, default=[],
                        help="screen group(s) holding the base-none and "
                             "base+principle prose cells (repeatable)")
    parser.add_argument("--principle", default="deontological+repair+generosity")
    parser.add_argument("--rollouts", type=Path, default=None,
                        help="run dir with rollouts/ -> trajectory + trace "
                             "statistics sections")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--allow-mixed", action="store_true")
    args = parser.parse_args()

    if not check_comparability(discover_run_dirs([args.group])) and not args.allow_mixed:
        raise SystemExit("ERROR: checkpoint cells differ in an undeclared "
                         "setting (see WARNINGs). --allow-mixed to proceed.")
    trained = trained_rows(args.group)
    if not trained:
        raise SystemExit(f"{args.group}: no trained prose/fixed PD cells")
    run = checkpoint_of(load_json(trained[0][1], "behavioral.json")["metadata"])[0]
    refs = reference_rows(args.reference, args.principle)
    for k in ("base", "teacher"):
        if k not in refs:
            print(f"WARNING: no {k} reference cell found in --reference groups")

    tables = [post_training_table(run, trained, refs, args.principle)]
    sections = []
    if args.rollouts:
        rows = windows(load_decisions(args.rollouts), args.window)
        tables.append(trajectory_table(run, rows, args.window))
        grams = principle_ngrams(get_moral_value(args.principle))
        traces = from_rollouts(args.rollouts, [s for s, _ in trained])
        tables.append(stats_table(stats(traces, grams), args.principle))

    out_dir = args.out or (args.group / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    md = [header(run, args.group, trained, refs, args.reference)]
    for t in tables:
        (tex_dir / f"{t.key.replace('-', '_')}.tex").write_text(to_latex(t))
        md.append(to_markdown(t))
    path = out_dir / f"results_{args.group.name}.md"   # named by experiment (group), like the screen names by model
    path.write_text("\n".join(md))
    print(f"saved -> {path}")
    print("\n".join(md[1:2]))


if __name__ == "__main__":
    main()
