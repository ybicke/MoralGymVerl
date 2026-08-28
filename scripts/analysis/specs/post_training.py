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
statistics at the evaluated steps (trace_measures.py; verbatim exemplars: traces_checkpoints_*.md / traces_training_*.md, written by this spec).

Output: <group>/analysis/results_<group>.md (+ tex/), mirroring the
screen layout; the group is the experiment name
(<model>-<game>-<algo>-<principle>), the training run is recorded in
the header and the manifest. Login node, stdlib only:
    /usr/bin/python3.11 scripts/analysis/make_results.py \
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval_cells import check_comparability, discover_run_dirs, load_cell, load_json  # noqa: E402
from measures import gap_pp  # noqa: E402
from specs.screen_2x2 import behavioral  # noqa: E402
from results_doc import (  # noqa: E402
    D_OPP, STATES4, Cell, Table, pct, plain, prompt_design_section,
    state_label, to_latex, to_markdown,
)
from training_trajectory import load_decisions, trajectory_table, windows  # noqa: E402
from trace_measures import (  # noqa: E402
    from_rollouts, parse_steps, render_exemplars,
    compact_stats_tables, from_cells, principle_ngrams, stats,
    vocab_windows,
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
            "state; 100 decisions per state (binomial s.e.\\ $\\leq$5 "
            "points; differences under $\\approx$14 points are not "
            "distinguishable). P(C) = mean of the four state rates, "
            "which the balanced design makes equal to the cell's overall "
            f"cooperation rate. {D_OPP} = P(C$\\mid$C$_O$) $-$ "
            "P(C$\\mid$D$_O$), the opponent-conditioning gap "
            "(reciprocity signature)."),
        stub="Policy",
        col_groups=[(None, [f"P(C$\\mid${state_label(s)})"]) for s in STATES4]
        + [(None, ["P(C)"]), (None, [D_OPP])],
        panels=[(None, rows)],
    )


# Presentation fields carried in each rollout's dumped ground truth.
# The eval's resolved choices live in behavioral.json ->
# opponents[0]["presentation"], so the two are directly comparable.
SURFACE_KEYS = ("coop_label", "defect_label", "matrix_layout",
                "agent_is_row", "opener_order")


def train_surface(run_dir: Path) -> Dict[str, set]:
    """Value sets of the presentation fields across training rollouts, so
    the training chapter can state what was randomized from the data."""
    values: Dict[str, set] = {k: set() for k in SURFACE_KEYS}
    n = 0
    for f in sorted((run_dir / "rollouts").glob("*.jsonl"),
                    key=lambda q: int(q.stem)):
        for line in open(f):
            g = json.loads(json.loads(line)["gts"])
            for k in SURFACE_KEYS:
                values[k].add(json.dumps(g.get(k)))
            n += 1
    values["_n"] = {n}
    return values


def checkpoint_chapter_spec(pres: Dict, meta: Dict) -> str:
    """Stated once; everything in chapter 1 uses it."""
    return "\n".join([
        "## 1. Checkpoint evaluation",
        "",
        "**Specification — applies to every table in this chapter.** The "
        "trained LoRA adapters re-run under the pre-training screen's "
        "protocol, so the rows are directly comparable with the screen's "
        "own cells. FIXED presentation: labels "
        f"`{pres.get('coop_label')}`/`{pres.get('defect_label')}`, "
        f"matrix_layout {pres.get('matrix_layout')}, agent_is_row "
        f"{pres.get('agent_is_row')}, opener order fixed. "
        f"{meta['game_type']} / {meta['representation']} / "
        f"{meta['protocol']} (fabricated history), balanced states, "
        f"{meta['num_episodes']} episodes per cell = 100 decisions per "
        f"state, T = {meta['eval_temperature']}, vs. random opponent. "
        "Trained rows carry NO moral text in the prompt.",
        "", "",
    ])


def training_chapter_spec(values: Dict[str, set], window: int) -> str:
    """Stated once; everything in chapter 2 uses it."""
    coop = json.loads(sorted(values["coop_label"])[0])
    defect = json.loads(sorted(values["defect_label"])[0])
    layouts = ", ".join(sorted(json.loads(x).__str__()
                               for x in values["matrix_layout"]))
    n = next(iter(values["_n"]))
    return "\n".join([
        "## 2. Training-time metrics (online rollouts)",
        "",
        "**Specification — applies to every table in this chapter.** "
        "Computed from the rollouts the policy generated while training "
        f"({n} rollouts, 256 per step); no model is re-run. RANDOMIZED "
        f"presentation: matrix_layout over {{{layouts}}}, role and "
        f"opener order randomized; labels `{coop}`/`{defect}` are NEVER "
        "randomized — the policy never saw chapter 1's label pair "
        "during training. Fabricated states are sampled at random, so "
        "they are UNBALANCED. Sampling T = 0.7.",
        "",
        "**Not comparable cell-by-cell with chapter 1** — different "
        "presentation surface and different state balance. Read this "
        "chapter for the SHAPE of learning over time; take behavioural "
        "numbers from chapter 1.",
        "", "",
    ])


def reference_provenance(refs: Dict[str, Path]) -> str:
    """Name the two reference cells exactly. Protocol is NOT restated
    here -- it is the surface line under Table P1, which these cells
    share."""
    lines = ["**Reference rows.** Table P1's four trained rows are the "
             "checkpoints, evaluated for this experiment. Its other two "
             "rows are UNTRAINED base-model cells taken from the "
             "pre-training screen, not re-run here:", "",
             "| Table P1 row | group | cell directory | job |",
             "|---|---|---|---|"]
    labels = {"base": "`base, no context`",
              "teacher": "`base + <principle> in context`"}
    for key in ("base", "teacher"):
        run_dir = refs.get(key)
        if run_dir is None:
            lines.append(f"| {labels[key]} | — | MISSING | — |")
            continue
        meta = load_json(run_dir, "behavioral.json")["metadata"]
        lines.append(f"| {labels[key]} | `{run_dir.parent.parent.name}` | "
                     f"`{run_dir.name}` | {meta.get('slurm_job_id', '—')} |")
    lines += ["", "They share Table P1's surface exactly; the rows differ "
              "only in `checkpoint` and `moral_value`. Verified "
              "key-by-key, but note `check_comparability` machine-checks "
              "the trained cells against each other only, not against "
              "these two."]
    return "\n".join(lines)


def header(run: str, group: Path, trained, refs: Dict[str, Path]) -> str:
    """Identity of the run only. The protocol lives in each table's
    surface line, so it is stated once per table and never here."""
    meta = load_json(trained[0][1], "behavioral.json")["metadata"]
    steps = ", ".join(str(s) for s, _ in trained)
    ck = meta.get("checkpoint", "")
    return "\n".join([
        f"# Post-training eval: {group.name}",
        "",
        f"Training run `{run}` on "
        f"{meta['base_model'].rsplit('/', 1)[-1]}; checkpoints at steps "
        f"{steps}, LoRA adapters merged from "
        f"`{ck.rsplit('/global_step_', 1)[0]}`.",
        "",
        reference_provenance(refs),
        "",
        "States are the fabricated previous round, subscripted A (agent) "
        f"and O (opponent): {plain(state_label('CD'))} = agent "
        "cooperated, opponent defected; C = cooperate. Generated by "
        "`scripts/analysis/make_results.py` (LaTeX in `tex/`).",
        "", "",
    ])


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", action="append", type=Path, default=[],
                        help="screen group(s) holding the base-none and "
                             "base+principle prose cells (repeatable)")
    parser.add_argument("--principle", default="deontological+repair+generosity")
    parser.add_argument("--rollouts", type=Path, default=None,
                        help="run dir with rollouts/ -> trajectory + trace "
                             "statistics sections and traces_training_*.md")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--steps", default="20,40,...,200",
                        help="rollout steps for traces_training_*.md "
                             "('a,b,...,end' expands)")
    parser.add_argument("--exemplars-k", type=int, default=8,
                        help="sample size per (step, state) for the trace "
                             "docs; the shortest is shown")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=2500)


def build(args: argparse.Namespace) -> Path:
    """Post-training checkpoint eval: Table P1 vs reference rows, trace
    statistics, training-rollout chapter; plus the two trace docs."""
    args.group = args.paths[0]
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

    meta = load_json(trained[0][1], "behavioral.json")["metadata"]
    eval_pres = behavioral(trained[0][1]).get("presentation") or {}

    out_dir = args.out or (args.group / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)

    def emit(table) -> str:
        (tex_dir / f"{table.key.replace('-', '_')}.tex").write_text(
            to_latex(table))
        return to_markdown(table)

    md = [header(run, args.group, trained, refs)]
    loaded = [load_cell(d) for _, d in trained] + [load_cell(d) for d in refs.values()]
    md.append(prompt_design_section([c for c in loaded if c is not None]))

    # --- chapter 1: the checkpoint eval (the publishable numbers) ---
    md.append(checkpoint_chapter_spec(eval_pres, meta))
    md.append(emit(post_training_table(run, trained, refs, args.principle)))
    # Trace statistics are reported ONCE, on the checkpoint-eval traces:
    # same policy, same surface and same episodes as Table P1, so the
    # rates line up with the behaviour directly above them.
    grams = principle_ngrams(get_moral_value(args.principle))
    for t in compact_stats_tables(stats(from_cells(args.group), grams),
                                  args.principle):
        md.append(emit(t))

    # --- chapter 2: what the training rollouts show ---
    if args.rollouts:
        md.append(training_chapter_spec(train_surface(args.rollouts),
                                        args.window))
        md.append(emit(trajectory_table(
            run, windows(load_decisions(args.rollouts), args.window),
            args.window,
            vocab=vocab_windows(args.rollouts, args.window, grams))))

    path = out_dir / f"results_{args.group.name}.md"   # named by experiment (group), like the screen names by model
    path.write_text("\n".join(md))
    print(f"saved -> {path}")

    # --- trace docs beside it (were traces_checkpoints.py / traces_training.py)
    exp = args.group.name
    traces = from_cells(args.group)
    steps = sorted({t.step for t in traces})
    surface = ("Checkpoint-eval cells (`behavioral.responses.jsonl`), screen surface: "
               "labels `action3`/`action4`, fixed layout, balanced states, 100 "
               "decisions per state; same episodes as Table P1 of results_*.md.")
    tpath = out_dir / f"traces_checkpoints_{exp}.md"
    tpath.write_text(render_exemplars(f"Checkpoint traces: {exp}", surface, steps, traces,
                                      args.principle, args.exemplars_k, args.seed,
                                      args.max_chars))
    print(f"saved -> {tpath}  ({len(traces)} traces, steps {steps})")
    if args.rollouts:
        steps = parse_steps(args.steps)
        traces = from_rollouts(args.rollouts, steps)
        surface = ("Online rollouts (`rollouts/<step>.jsonl`), TRAINING surface: labels "
                   "`action1`/`action2`, randomized layout and role, fabricated previous "
                   "state sampled at random (unbalanced). Not comparable cell-by-cell "
                   "with the checkpoint evals; read for the shape over steps.")
        tpath = out_dir / f"traces_training_{exp}.md"
        tpath.write_text(render_exemplars(f"Training traces: {args.rollouts.name}", surface,
                                          steps, traces, args.principle, args.exemplars_k,
                                          args.seed, args.max_chars))
        print(f"saved -> {tpath}  ({len(traces)} traces, steps {steps})")
    return path
