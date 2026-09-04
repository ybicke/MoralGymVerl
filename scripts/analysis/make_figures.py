#!/usr/bin/env python3.11
"""Report figures + LaTeX number macros for the results document.

Companion CLI to make_results.py: make_results emits the markdown/tex
tables, this emits the figures (PDF for LaTeX, PNG for viewing) and
generated/numbers.tex, all computed from the same data layer
(training_trajectory.py, eval_cells.py) so a figure and a table can
never disagree on a number.

Figures land with the experiment: --analysis-dir/figures/<name>.pdf|png
(+ numbers.tex beside them), versioned alongside the results docs. When
the Overleaf clone exists (--report-dir, default ~/moralgym-report), the
PDFs and generated/numbers.tex are mirrored there for the LaTeX doc.

Login node:
    /usr/bin/python3.11 scripts/analysis/make_figures.py training-curves \\
        --run-dir ~/logs_verl/runs/grpo_deon_tft_200 [--window 5]
    /usr/bin/python3.11 scripts/analysis/make_figures.py ckpt-ladder \\
        --ladder eval_results/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder \\
        --reference eval_results/teacher_signal/qwen3_8b/classic/single_turn_screen \\
        --reference eval_results/teacher_signal/qwen3_8b/classic/pd_generosity_arm \\
        --principle deontological+repair+generosity

Adding a figure = one function + a FIGURES entry; keep data loading in
the shared modules and only geometry here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from figure_style import (  # noqa: E402
    ACCENT, INK, INK_MUTED, RUN_COLORS, STATE_COLORS, STATE_TEX, WIDTHS,
    apply_style, clean_axes, direct_labels, save,
)
from training_trajectory import STATES, load_decisions, windows  # noqa: E402
from eval_cells import discover_run_dirs, load_cell  # noqa: E402
from specs.post_training import checkpoint_of, reference_rows  # noqa: E402
from measures import (  # noqa: E402
    longest_overlap, normative_hit, principle_ngrams, principle_overlap,
    words as measure_words,
)
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402
from trace_measures import from_cells, select_exemplars  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def cell_p_c(cell) -> Dict[str, float]:
    """P(C|state) in %, illegal excluded from the denominator."""
    c: Dict[str, int] = defaultdict(int)
    t: Dict[str, int] = defaultdict(int)
    for d in cell.decisions:
        if d.state in ("CC", "CD", "DC", "DD") and d.move != "illegal":
            t[d.state] += 1
            c[d.state] += d.move == "C"
    return {s: 100 * c[s] / t[s] for s in ("CC", "CD", "DC", "DD") if t[s]}


# ---------------------------------------------------------------------------
# figure: training curves (per-state P(C) from rollout dumps)
# ---------------------------------------------------------------------------

def load_group_props(run_dir: Path, window: int) -> List[dict]:
    """Per window and state: the per-prompt-group cooperation fractions
    (each GRPO group = 16 rollouts of one prompt = one state). Groups
    are the independent unit -- rollouts within a group share a prompt
    -- so means and CIs over groups are the honest training statistics."""
    acc: Dict[int, Dict[str, list]] = defaultdict(
        lambda: defaultdict(list))
    for f in sorted((run_dir / "rollouts").glob("*.jsonl"),
                    key=lambda pth: int(pth.stem)):
        groups: Dict[str, list] = {}
        step = None
        for line in open(f):
            row = json.loads(line)
            step = int(row["step"])
            gts = json.loads(row["gts"])
            g = groups.setdefault(row["input"],
                                  [gts["fab_agent"] + gts["fab_opp"], 0, 0])
            if float(row["parse_fail_rate"]) > 0:
                continue
            g[1] += float(row["cooperation_rate"]) > 0
            g[2] += 1
        w = (step - 1) // window
        for state, n_c, n in groups.values():
            if n:
                acc[w][state].append(n_c / n)
    return [{"first": w * window + 1, "last": (w + 1) * window,
             "props": {st: acc[w].get(st, []) for st in STATES}}
            for w in sorted(acc)]


def fig_training_curves(args, out_dir: Path) -> None:
    run_dir = Path(args.run_dir).expanduser()
    rows = windows(load_decisions(run_dir), args.window)
    mid = [(r["first"] + r["last"]) / 2 for r in rows]

    fig, ax = plt.subplots(figsize=(WIDTHS["wide"], 2.9))

    for s in STATES:
        ys = [100 * r["p"][s] if r["p"][s] is not None else None for r in rows]
        ax.plot(mid, ys, color=STATE_COLORS[s])
    direct_labels(
        ax, [(100 * rows[-1]["p"][s], STATE_TEX[s], STATE_COLORS[s])
             for s in STATES if rows[-1]["p"][s] is not None],
        x=mid[-1], min_gap=7)
    ax.legend([STATE_TEX[s] for s in STATES], loc="upper left",
              ncols=2, columnspacing=1.0, handlelength=1.4)
    ax.set_ylabel(r"$P(\mathrm{C}\mid\mathrm{state})$ (%)")
    ax.set_ylim(-3, 103)
    clean_axes(ax)

    ax.set_xlabel("training step")

    illegal = max(r["illegal"] for r in rows)
    print(f"max illegal rate over windows: {100 * illegal:.2f}%")
    for p in save(fig, out_dir / "figures",
                  f"training_curves_{run_dir.name}"):
        print(f"wrote {p}")


# ---------------------------------------------------------------------------
# figure: checkpoint ladder (screen protocol, balanced states)
# ---------------------------------------------------------------------------

def load_ladder(ladder: Path, reference: List[Path], principle: str):
    """(steps, per-state curves incl. base at 0, teacher P(C|state)).
    With no reference group, curves start at the first checkpoint."""
    trained = {}
    for run_dir in discover_run_dirs([ladder]):
        cell = load_cell(run_dir)
        if cell is None:
            continue
        run, step = checkpoint_of(cell.meta)
        if run != "base" and cell.arm == "none":
            trained[step] = cell_p_c(cell)
    if not trained:
        raise SystemExit(f"{ladder}: no trained cells")
    refs = reference_rows(reference, principle) if reference else {}
    if reference and "base" not in refs:
        raise SystemExit(f"no base cell found under {reference}")
    base = cell_p_c(load_cell(refs["base"])) if refs else None
    teacher = cell_p_c(load_cell(refs["teacher"])) if "teacher" in refs else None
    steps = sorted(trained)
    curves = {s: ([base[s]] if base else []) + [trained[k][s] for k in steps]
              for s in ("CC", "CD", "DC", "DD")}
    return steps, curves, teacher, base is not None


def fig_ckpt_ladder(args, out_dir: Path) -> None:
    ladder = Path(args.ladder[0].split("=", 1)[-1])
    steps, curves, teacher, has_base = load_ladder(
        ladder, [Path(p) for p in args.reference], args.principle)
    xs = ([0] if has_base else []) + steps

    fig, ax = plt.subplots(figsize=(WIDTHS["wide"], 2.9))
    for s, ys in curves.items():
        ax.plot(xs, ys, color=STATE_COLORS[s], marker="o", markersize=4)
    direct_labels(ax, [(ys[-1], STATE_TEX[s], STATE_COLORS[s])
                       for s, ys in curves.items()],
                  x=xs[-1], min_gap=7)

    if teacher:
        xt = xs[-1] + (xs[-1] - xs[0]) * 0.18
        ax.axvline(xs[-1] + (xt - xs[-1]) / 2, color=INK_MUTED,
                   linewidth=0.6, linestyle=(0, (2, 3)))
        for s, y in teacher.items():
            ax.plot([xt], [y], marker="o", markersize=5, mfc="white",
                    mec=STATE_COLORS[s], mew=1.4, linestyle="none")
        ax.set_xticks(xs + [xt], (["base"] if has_base else [])
                      + [str(k) for k in steps] + ["base +\nprinciple"])
    else:
        ax.set_xticks(xs, (["base"] if has_base else [])
                      + [str(k) for k in steps])

    ax.legend([STATE_TEX[s] for s in curves], loc="center left",
              handlelength=1.4)
    ax.set_ylabel(r"$P(\mathrm{C}\mid\mathrm{state})$ (%)")
    ax.set_ylim(-3, 103)
    ax.set_xlabel("checkpoint (training step)")
    clean_axes(ax)

    name = f"ckpt_ladder_{ladder.parents[1].name}"
    for p in save(fig, out_dir / "figures", name):
        print(f"wrote {p}")
    if args.macro_prefix:
        write_numbers(steps, curves, teacher, out_dir, args.macro_prefix)


def write_numbers(steps, curves, teacher, out_dir: Path,
                  prefix: str) -> None:
    """generated/numbers_<prefix>.tex: the headline quantities as
    macros, so the running text cites the same numbers the figures
    draw. One file and macro namespace per experiment (LaTeX macro
    names allow letters only)."""
    if not (prefix.isalpha()):
        raise SystemExit(f"--macro-prefix must be letters only: {prefix}")
    def d_opp(vals: Dict[str, float]) -> float:
        return (vals["CC"] + vals["DC"]) / 2 - (vals["CD"] + vals["DD"]) / 2

    final = {s: ys[-1] for s, ys in curves.items()}
    base = {s: ys[0] for s, ys in curves.items()}
    macros = {f"{prefix}finalStep": str(steps[-1])}
    for s in curves:
        macros[f"{prefix}base{s}"] = f"{base[s]:.0f}"
        macros[f"{prefix}final{s}"] = f"{final[s]:.0f}"
    macros[f"{prefix}baseDeltaOpp"] = f"{d_opp(base):+.0f}"
    macros[f"{prefix}finalDeltaOpp"] = f"{d_opp(final):+.0f}"
    if teacher:
        for s, y in teacher.items():
            macros[f"{prefix}teacher{s}"] = f"{y:.0f}"
        macros[f"{prefix}teacherDeltaOpp"] = f"{d_opp(teacher):+.0f}"

    out = out_dir / "generated" / f"numbers_{prefix}.tex"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["% generated by scripts/analysis/make_figures.py -- do not edit"]
    lines += [rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(macros.items())]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(macros)} macros)")


# ---------------------------------------------------------------------------
# cross-run figures
# ---------------------------------------------------------------------------

def parse_labeled(entries: List[str]) -> List[tuple]:
    """['label=path', ...] -> [(label, Path)], label defaults to dir name."""
    out = []
    for e in entries:
        label, _, path = e.rpartition("=")
        path = Path(path).expanduser()
        out.append((label or path.name, path))
    return out


def fig_training_grid(args, out_dir: Path) -> None:
    """Small multiples of the per-state training curves: one panel per
    run (--run label=rollout-dir, repeated), shared axes and state
    colors, so rows/columns compare across models and algorithms
    without crowding one axes."""
    runs = parse_labeled(args.run)
    ncols = min(args.ncols, len(runs))
    nrows = -(-len(runs) // ncols)
    fig, axes = plt.subplots(
        nrows, ncols, sharey=True,
        figsize=(WIDTHS["wide"] * ncols / 2, 1.75 * nrows + 0.4),
        squeeze=False, gridspec_kw={"hspace": 0.55, "wspace": 0.08})

    for i, (label, run_dir) in enumerate(runs):
        ax = axes[i // ncols][i % ncols]
        rows = load_group_props(run_dir, args.window)
        mid = [(r["first"] + r["last"]) / 2 for r in rows]
        for s in STATES:
            mean, lo, hi = [], [], []
            for r in rows:
                props = r["props"][s]
                if not props:
                    mean.append(None); lo.append(None); hi.append(None)
                    continue
                m = sum(props) / len(props)
                sd = (sum((x - m) ** 2 for x in props)
                      / max(1, len(props) - 1)) ** 0.5
                ci = 1.96 * sd / len(props) ** 0.5
                mean.append(100 * m)
                lo.append(100 * max(0.0, m - ci))
                hi.append(100 * min(1.0, m + ci))
            ax.plot(mid, mean, color=STATE_COLORS[s], linewidth=1.2)
            ok = [j for j, v in enumerate(mean) if v is not None]
            ax.fill_between([mid[j] for j in ok], [lo[j] for j in ok],
                            [hi[j] for j in ok], color=STATE_COLORS[s],
                            alpha=0.15, linewidth=0)
        ax.set_title(label, fontsize=8)
        ax.set_ylim(-3, 103)
        clean_axes(ax)
        if i // ncols == nrows - 1:
            ax.set_xlabel("training step", fontsize=7.5)
        if i % ncols == 0:
            ax.set_ylabel(r"$P(\mathrm{C}\mid\mathrm{state})$ (%)",
                          fontsize=7.5)
    for j in range(len(runs), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.legend([plt.Line2D([], [], color=STATE_COLORS[s]) for s in STATES],
               [STATE_TEX[s] for s in STATES], loc="upper center",
               ncols=4, frameon=False,
               bbox_to_anchor=(0.5, 1 + 0.14 / nrows), fontsize=8)
    for pth in save(fig, out_dir / "figures", f"training_grid_{args.name}"):
        print(f"wrote {pth}")


def fig_ladder_grid(args, out_dir: Path) -> None:
    """Small multiples of checkpoint ladders, one panel per run
    (--ladder label=dir repeated). Group one algorithm per figure
    (--name grpo/sdpo): panels then show replication across models,
    and the contrast BETWEEN the figures is the algorithm effect.
    --reference groups are matched to each panel's model for the
    base point at step 0."""
    ladders = parse_labeled(args.ladder)
    refs = [Path(p) for p in args.reference]
    ncols = min(args.ncols, len(ladders))
    nrows = -(-len(ladders) // ncols)
    fig, axes = plt.subplots(
        nrows, ncols, sharey=True,
        figsize=(WIDTHS["wide"] * ncols / 2, 1.9 * nrows + 0.4),
        squeeze=False, gridspec_kw={"hspace": 0.5, "wspace": 0.08})

    for i, (label, ladder) in enumerate(ladders):
        ax = axes[i // ncols][i % ncols]
        model_refs = [r for r in refs
                      if r.parts[-3].split("_")[0] in ladder.parents[1].name]
        steps, curves, _, has_base = load_ladder(
            ladder, model_refs, args.principle)
        xs = ([0] if has_base else []) + steps
        for st, ys in curves.items():
            ax.plot(xs, ys, color=STATE_COLORS[st], marker="o",
                    markersize=3, linewidth=1.2)
        ax.set_xticks(xs, (["base"] if has_base else [])
                      + [str(k) for k in steps], fontsize=7)
        ax.set_title(label, fontsize=8)
        ax.set_ylim(-3, 103)
        clean_axes(ax)
        if i // ncols == nrows - 1:
            ax.set_xlabel("checkpoint (training step)", fontsize=7.5)
        if i % ncols == 0:
            ax.set_ylabel(r"$P(\mathrm{C}\mid\mathrm{state})$ (%)",
                          fontsize=7.5)
    for j in range(len(ladders), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.legend([plt.Line2D([], [], color=STATE_COLORS[st]) for st in STATE_COLORS],
               [STATE_TEX[st] for st in STATE_COLORS], loc="upper center",
               ncols=2 if ncols == 1 else 4, frameon=False,
               bbox_to_anchor=(0.5, 1 + 0.16 / nrows), fontsize=8)
    for pth in save(fig, out_dir / "figures", f"ladder_grid_{args.name}"):
        print(f"wrote {pth}")
    if args.macro_prefix and len(ladders) == 1:
        write_numbers(steps, curves, None, out_dir, args.macro_prefix)


def trace_language_table(args, out_dir: Path) -> None:
    """Two tables: tables/trace_language_pd.tex (normative-language and
    verbatim-recitation rates per PD checkpoint, pooled over the four
    states) and tables/trace_language_pgg.tex (the same two measures on
    the held-out PGG transfer cells, base vs final checkpoint).
    Normative = trace contains >=1 of the 12 reviewed stems; recitation
    = reproduces >=6 consecutive words of the principle. Computed from
    the same cells as the checkpoint figures."""
    BS, NL = chr(92), chr(10)
    grams = principle_ngrams(get_moral_value(args.principle))

    def rates(cell):
        traces = [d.trace for d in cell.decisions]
        return (100 * sum(map(normative_hit, traces)) / len(traces),
                100 * sum(principle_overlap(t, grams) for t in traces)
                / len(traces))

    def table(caption, label, groups, hdr, rows):
        lines = [
            BS + "begin{table}[H]", BS + "centering",
            BS + "caption{" + caption + "}",
            BS + "label{" + label + "}",
            BS + "begin{tabular}{l" + "r" * len(hdr) + "}",
            BS + "toprule"]
        col = 2
        heads, mids = [], []
        for gname, n in groups:
            heads.append(BS + "multicolumn{" + str(n) + "}{c}{" + gname + "}")
            mids.append(BS + "cmidrule(lr){" + str(col) + "-"
                        + str(col + n - 1) + "}")
            col += n
        lines += [" & " + " & ".join(heads) + " " + BS + BS,
                  "".join(mids),
                  "Run & " + " & ".join(hdr) + " " + BS + BS,
                  BS + "midrule"]
        lines += [" & ".join(r) + " " + BS + BS for r in rows]
        lines += [BS + "bottomrule", BS + "end{tabular}", BS + "end{table}"]
        return NL.join(lines) + NL

    tdir = out_dir / "generated" / "tables"
    tdir.mkdir(parents=True, exist_ok=True)
    note = ("% generated by scripts/analysis/make_figures.py"
            " -- do not edit" + NL)

    # --- PD table -------------------------------------------------------
    cols = [None if c == "final" else int(c)
            for c in args.steps.split(",")]
    hdr = ["base" if c == 0 else "final" if c is None else str(c)
           for c in cols]
    rows = []
    for label, ladder in parse_labeled(args.ladder):
        cells, base_cell = {}, None
        for run_dir in discover_run_dirs([ladder]):
            cell = load_cell(run_dir)
            if cell is None:
                continue
            run, step = checkpoint_of(cell.meta)
            if run != "base" and cell.arm == "none":
                cells[step] = cell
        model_refs = [Path(r) for r in args.reference
                      if Path(r).parts[-3].split("_")[0]
                      in ladder.parents[1].name]
        refs = (reference_rows(model_refs, args.principle)
                if model_refs else {})
        if "base" in refs:
            base_cell = load_cell(refs["base"])
        final = max(cells)
        row = [label]
        for want_norm in (True, False):
            for c in cols:
                cell = base_cell if c == 0 else cells.get(
                    final if c is None else c)
                row.append("--" if cell is None else
                           f"{rates(cell)[want_norm - 1]:.0f}")
        rows.append(row)
    cap = (r"Moral language in the PD reasoning traces, pooled over the"
           r" four states (400 traces per cell, checkpoint-eval surface;"
           r" the principle text is never in these prompts)."
           r" \emph{Normative} = the trace contains at least one of 12"
           r" moral word stems (good faith, trust, exploit, moral, ethic,"
           r" principle, fair, reciproc, wrong, obligat, betray, honest);"
           r" the untrained base rate is payoff-sense hits, mostly"
           r" `exploit'. \emph{Recites} = the trace quotes at least 6"
           r" consecutive words of the teacher principle verbatim --- a"
           r" hit means word-for-word quoted \emph{clauses} (median run"
           r" 8, max 21 of the principle's ${\sim}90$ words), not the"
           r" full text, and a faithful paraphrase scores 0. `final' ="
           r" the run's last evaluated checkpoint.").replace(BS + BS, BS)
    (tdir / "trace_language_pd.tex").write_text(note + table(
        cap, "tab:trace-language-pd",
        [("normative (" + BS + "%)", len(cols)),
         ("recites (" + BS + "%)", len(cols))],
        hdr + hdr, rows))
    print(f"wrote {tdir / 'trace_language_pd.tex'}")
    for r in rows:
        print("  " + "  ".join(f"{v:>5}" for v in r))

    # --- PGG transfer table --------------------------------------------
    rows = []
    lad = dict(parse_labeled(args.ladder))
    for label, group in parse_labeled(args.transfer):
        run_name = (lad[label].parents[1].name if label in lad else None)
        base_cell, best = None, (None, -1)
        for run_dir in discover_run_dirs([group]):
            cell = load_cell(run_dir)
            if cell is None:
                continue
            run, step = checkpoint_of(cell.meta)
            if run == "base":
                base_cell = cell
            elif (run_name is None or run == run_name) and step > best[1]:
                best = (cell, step)
        if base_cell is None:
            # base from the model's PGG screen group (--pgg-reference)
            for ref in args.pgg_reference:
                ref = Path(ref)
                if ref.parts[-3] not in str(group):
                    continue
                for run_dir in discover_run_dirs([ref]):
                    cell = load_cell(run_dir)
                    if (cell is not None and cell.arm == "none"
                            and checkpoint_of(cell.meta)[0] == "base"):
                        base_cell = cell
                        break
                break
        row = [label]
        for want_norm in (True, False):
            for cell in (base_cell, best[0]):
                row.append("--" if cell is None else
                           f"{rates(cell)[want_norm - 1]:.0f}")
        rows.append(row)
    cap = (r"The same two measures on the held-out PGG transfer traces"
           r" (400 traces per cell, no moral text in any prompt): the"
           r" untrained base model and each run's last"
           r" transfer-evaluated checkpoint.").replace(BS + BS, BS)
    (tdir / "trace_language_pgg.tex").write_text(note + table(
        cap, "tab:trace-language-pgg",
        [("normative (" + BS + "%)", 2), ("recites (" + BS + "%)", 2)],
        ["base", "final", "base", "final"], rows))
    print(f"wrote {tdir / 'trace_language_pgg.tex'}")
    for r in rows:
        print("  " + "  ".join(f"{v:>5}" for v in r))


def prompt_panels(args, out_dir: Path) -> None:
    """generated/prompt_panels.tex: the verbatim game prompt (from a
    checkpoint-eval cell, so exactly what the evaluated models saw) and
    the moral-principle text (from moral_values.py), each as a framed
    listing. Both channels share the game prompt; the principle appears
    only in the SDPO teacher context."""
    BS, NL = chr(92), chr(10)
    ladder = Path(args.ladder[0].split("=", 1)[-1])
    for run_dir in discover_run_dirs([ladder]):
        cell = load_cell(run_dir)
        if cell is not None:
            break
    prompt = next((d.prompt for d in cell.decisions if d.state == "CC"),
                  cell.decisions[0].prompt)
    sfx = "" if args.name == "all" else f"_{args.name}"

    tdir = out_dir / "generated" / "panels"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"game_prompt{sfx}.txt").write_text(prompt + NL)

    lines = [
        "% generated by scripts/analysis/make_figures.py -- do not edit",
        BS + "lstinputlisting[title={Game prompt (one evaluation episode;"
        " the fabricated previous round varies by episode)}]"
        "{generated/panels/game_prompt" + sfx + ".txt}",
    ]
    if not args.game_only:
        principle = get_moral_value(args.principle).strip()
        (tdir / "principle.txt").write_text(principle + NL)
        lines.append(
            BS + "lstinputlisting[title={Moral principle"
            " `" + args.principle.replace("_", " ") + "' --- shown ONLY in"
            " the SDPO teacher context, never to the trained policy or in"
            " any evaluation prompt}]{generated/panels/principle.txt}")
    out = out_dir / "generated" / f"prompt_panels{sfx}.tex"
    out.write_text(NL.join(lines) + NL)
    print(f"wrote {out}")


def trace_panels(args, out_dir: Path) -> None:
    """generated/trace_panels.tex: curated reasoning-trace exemplars
    (--pick 'label=ckpt_ladder_dir:step:state', repeated), selected by
    trace_measures.select_exemplars -- the same function and defaults
    (shortest of k=8, seed 0) the traces_checkpoints_*.md docs use, so
    report and docs show byte-identical traces."""
    BS, NL = chr(92), chr(10)
    tdir = out_dir / "generated" / "panels"
    tdir.mkdir(parents=True, exist_ok=True)
    sels = {}
    entries = []
    marks = args.mark + [""] * (len(args.pick) - len(args.mark))
    for pickspec, mark in zip(args.pick, marks):
        label, _, rest = pickspec.rpartition("=")
        group, step, state = rest.rsplit(":", 2)
        group = Path(group).expanduser()
        if group not in sels:
            traces = from_cells(group)
            steps = sorted({t.step for t in traces})
            sel = select_exemplars(traces=traces, steps=steps, k=8, seed=0)
            by_step = defaultdict(list)
            for t in traces:
                by_step[t.step].append(t)
            sel = type("Sel", (), {"get": sel.get,
                                   "traces_by_step": dict(by_step)})()
            sels[group] = sel
        if state == "max-recite":
            # the step's longest verbatim run of the principle, any state
            # (the traces_*.md docs' "longest recitation" exemplar); the
            # run is auto-highlighted, so no --mark is needed.
            pw = measure_words(get_moral_value(args.principle))
            pool = [t for t in sels[group].traces_by_step[int(step)]]
            t = max(pool, key=lambda t: longest_overlap(t.text, pw)[0])
        else:
            t = sels[group].get((int(step), state))
        if t is None:
            raise SystemExit(f"no exemplar for {label} step {step} {state}")
        body = t.text.strip()
        if len(body) > 2500:
            body = body[:2500] + " [...]"
        if state == "max-recite":
            pw = measure_words(get_moral_value(args.principle))
            L, i = longest_overlap(t.text, pw)
            span_words = measure_words(t.text)[i:i + L]
            pat = r"\W+".join(re.escape(w) for w in span_words)
            m = re.search(pat, body, re.I)
            if m:
                body = (body[:m.start()] + "~~" + body[m.start():m.end()]
                        + "~~" + body[m.end():])
        elif mark:
            kind, _, span = mark.partition("=")
            d = {"fail": "@@", "recite": "~~"}[kind]
            if span not in body:
                raise SystemExit(f"mark not found in {label}: {span[:40]}")
            body = body.replace(span, d + span + d, 1)
        import re as _re
        slug = _re.sub(r"[^A-Za-z0-9]+", "_", f"{label}_{step}_{state}")
        (tdir / f"{slug}.txt").write_text(body + NL)
        if state == "max-recite":
            st_tex = "longest verbatim recitation ({} words)".format(
                longest_overlap(t.text, measure_words(
                    get_moral_value(args.principle)))[0])
        else:
            st_tex = ("state " + BS + "(" + BS + "mathrm{" + state[0]
                      + "_A " + state[1] + "_O}" + BS + ")")
        entries.append(
            BS + "lstinputlisting[title={" + label + " --- step " + step
            + ", " + st_tex + ", move " + t.move
            + "}]{generated/panels/" + slug + ".txt}")
    (out_dir / "generated" / "trace_panels.tex").write_text(
        "% generated by scripts/analysis/make_figures.py -- do not edit"
        + NL + (NL + NL).join(entries) + NL)
    print(f"wrote {out_dir / 'generated' / 'trace_panels.tex'} "
          f"({len(entries)} panels)")


def fig_dopp_compare(args, out_dir: Path) -> None:
    """One line per trained run: the opponent-conditioning gap
    D_opp = P(C|f_O=C) - P(C|f_O=D) over checkpoints (the reciprocity
    signature), computed from each run's ckpt-ladder eval. --ladder
    label=dir repeated; --reference groups supply each MODEL's base
    cell and are searched for every ladder."""
    ladders = parse_labeled(args.ladder)
    refs = [Path(p) for p in args.reference]

    def d_opp(v):
        return (v["CC"] + v["DC"]) / 2 - (v["CD"] + v["DD"]) / 2

    fig, ax = plt.subplots(figsize=(WIDTHS["wide"], 2.6))
    ends = []
    for i, (label, ladder) in enumerate(ladders):
        model_refs = [r for r in refs
                      if r.parts[-3].split("_")[0] in ladder.parents[1].name]
        steps, curves, _, has_base = load_ladder(
            ladder, model_refs, args.principle)
        xs = ([0] if has_base else []) + steps
        ys = [d_opp({s: curves[s][j] for s in curves})
              for j in range(len(xs))]
        color = RUN_COLORS[i % len(RUN_COLORS)]
        ax.plot(xs, ys, color=color, marker="o", markersize=3.5)
        ends.append((ys[-1], label, color))
    direct_labels(ax, ends, x=max(x for _, l in ladders for x in [200]),
                  min_gap=6, fontsize=7.5)
    ax.legend([l for l, _ in ladders], loc="upper left", fontsize=7.5)
    ax.set_ylabel(r"$\Delta_{\mathrm{opp}}$ (pp)")
    ax.set_xlabel("checkpoint (training step)")
    ax.axhline(0, color=INK_MUTED, linewidth=0.6)
    clean_axes(ax)
    for pth in save(fig, out_dir / "figures", "dopp_compare"):
        print(f"wrote {pth}")


# ---------------------------------------------------------------------------

def fig_transfer_grid(args, out_dir: Path) -> None:
    """Cross-model transfer: --group <transfer group dir> repeated (one per
    model), --reference screen groups for the in-context rows. Columns are
    every run across the groups, so the same figure shows replication
    across models and the GRPO-vs-SDPO contrast within one."""
    from transfer_figures import fig_curves, fig_final, fig_pooled, load_transfer
    runs = []
    for g in args.group:
        runs += load_transfer(Path(g).expanduser(),
                              [Path(r) for r in args.reference],
                              [args.principle] if args.principle else None)
    if not runs:
        raise SystemExit("no transfer runs found under --group dirs")
    for pth in (fig_pooled(runs, out_dir, args.name) + fig_final(runs, out_dir, args.name)
                + fig_curves(runs, out_dir, args.name)):
        print(f"wrote {pth}")


def mirror_to_report(out_dir: Path, report_dir: Path) -> None:
    """Copy PDFs + numbers.tex into the Overleaf clone, if it exists."""
    if not (report_dir / ".git").is_dir():
        print(f"note: {report_dir} not present, nothing mirrored")
        return
    import shutil
    for src in sorted((out_dir / "figures" / "pdf").glob("*.pdf")):
        dst = report_dir / "figures" / src.name
        dst.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        print(f"mirrored {dst}")
    gen = out_dir / "generated"
    for num in (sorted(gen.glob("numbers_*.tex"))
                + sorted(gen.glob("tables/*.tex"))
                + sorted(gen.glob("*panels*.tex"))
                + sorted(gen.glob("panels/*.txt"))):
        dst = report_dir / "generated" / num.relative_to(gen)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(num, dst)
        print(f"mirrored {dst}")


FIGURES = {
    "training-curves": fig_training_curves,
    "training-grid": fig_training_grid,
    "ladder-grid": fig_ladder_grid,
    "dopp-compare": fig_dopp_compare,
    "trace-table": trace_language_table,
    "prompt-panels": prompt_panels,
    "trace-panels": trace_panels,
    "ckpt-ladder": fig_ckpt_ladder,
    "transfer-grid": lambda a, o: fig_transfer_grid(a, o),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("figure", choices=sorted(FIGURES))
    ap.add_argument("--analysis-dir",
                    help="output anchor; default: <ladder>/analysis for "
                         "ckpt-ladder, <run-dir experiment's analysis> must "
                         "be given for training-curves")
    ap.add_argument("--report-dir", default="~/moralgym-report",
                    help="Overleaf clone; PDFs + numbers.tex are mirrored "
                         "there when it exists (default: ~/moralgym-report)")
    ap.add_argument("--run-dir", help="training run dir with rollouts/")
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--ladder", action="append", default=[],
                    help="ckpt_ladder eval group dir; for dopp-compare "
                         "repeat as label=dir")
    ap.add_argument("--run", action="append", default=[],
                    help="training-grid: label=rollout-run-dir, repeated")
    ap.add_argument("--ncols", type=int, default=2)
    ap.add_argument("--group", action="append", default=[],
                    help="transfer-grid: eval_results/transfer/<model>/"
                         "<family>/<experiment> dir, repeated")
    ap.add_argument("--steps", default="0,60,120,final",
                    help="trace-table PD columns (0=base, 'final'=last)")
    ap.add_argument("--pgg-reference", action="append", default=[],
                    help="trace-table: PGG screen group(s) supplying a "
                         "model's base cell when the transfer group has "
                         "none")
    ap.add_argument("--transfer", action="append", default=[],
                    help="trace-table: label=transfer_group for the PGG "
                         "column pair; a label without a --ladder makes a "
                         "transfer-only row")
    ap.add_argument("--game-only", action="store_true",
                    help="prompt-panels: emit only the game prompt panel")
    ap.add_argument("--pick", action="append", default=[],
                    help="trace-panels: label=ckpt_ladder_dir:step:state, "
                         "repeated")
    ap.add_argument("--mark", action="append", default=[],
                    help="trace-panels: per pick (positional): "
                         "fail=<substr> or recite=<substr> or ''")
    ap.add_argument("--name", default="all",
                    help="ladder-grid: output filename suffix, e.g. the "
                         "algorithm the grid groups (grpo/sdpo)")
    ap.add_argument("--reference", action="append", default=[],
                    help="screen group dir(s) holding the base cells")
    ap.add_argument("--principle", default="deontological+repair+generosity",
                    help="moral_value of the optional base+principle "
                         "reference cell; drawn only if a --reference "
                         "group contains it")
    ap.add_argument("--macro-prefix", default="",
                    help="letters-only namespace for numbers_<prefix>.tex "
                         "macros; one per experiment")
    args = ap.parse_args()

    apply_style()
    if args.analysis_dir:
        out_dir = Path(args.analysis_dir).expanduser()
    elif args.figure == "ckpt-ladder":
        out_dir = Path(args.ladder[0].split("=", 1)[-1]) / "analysis"
    elif args.figure in ("training-grid", "ladder-grid", "dopp-compare",
                         "trace-table", "prompt-panels", "trace-panels"):
        out_dir = Path("eval_results/post_training/comparison/analysis")
    elif args.figure == "transfer-grid":
        out_dir = Path("eval_results/transfer/comparison/analysis")
    else:
        raise SystemExit("training-curves needs --analysis-dir "
                         "(the experiment's analysis dir)")
    FIGURES[args.figure](args, out_dir)
    mirror_to_report(out_dir, Path(args.report_dir).expanduser())


if __name__ == "__main__":
    main()
