"""Figures for held-out-game transfer (eval_results/transfer/, PGG single
round). Shared by the per-model results document (specs/post_training_pgg)
and the cross-model grid in make_figures.py, so both draw the same
geometry from the same data layer.

Two figures:
  curves   small multiples, rows = own previous move (C / D), columns =
           training runs; one line per checkpoint shaded light-to-full by
           step, base in grey, in-context rows dotted. Reads the SHAPE of
           what transferred (flat = unconditional, rising = conditional).
  pooled   pooled P(C) against training step, one line per run, base at
           step 0, in-context rows as dotted levels. Reads the LEVEL.

Data come straight from behavioral.json (published curve + pooled rate);
the tables in the results doc re-derive the same numbers from the traces
and gate against drift, so a figure cannot disagree with its table.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from eval_cells import discover_run_dirs, load_json  # noqa: E402
from figure_style import (  # noqa: E402
    ACCENT, INK, INK_MUTED, REF_BASE, REF_CONTEXT, RUN_COLORS, WIDTHS,
    clean_axes, direct_labels as _labels, footnote, save,
)

CKPT_RE = re.compile(r"/([^/]+)/global_step_(\d+)/")
OWN = ("C", "D")


class Run:
    """One training run's transfer points plus its model's references."""

    def __init__(self, name: str, model: str):
        self.name, self.model = name, model
        self.points: Dict[int, Dict] = {}         # step -> opponents[0] block
        self.base: Optional[Dict] = None
        self.context: Dict[str, Dict] = {}        # moral_value -> block

    @property
    def channel(self) -> str:
        f = self.name.split("_")
        return f"{f[2].upper()} {f[4]}" if len(f) >= 6 else self.name

    @property
    def steps(self) -> List[int]:
        return sorted(self.points)


def _model(meta: Dict) -> str:
    return meta["base_model"].rsplit("/", 1)[-1]


def load_transfer(group: Path, references: List[Path],
                  context: Optional[List[str]] = None) -> List[Run]:
    """Runs of one transfer group. Base and in-context rows come from the
    group's own `checkpoint: base` cells first, then from --reference
    screen groups of the same model."""
    runs: Dict[str, Run] = {}
    base: Optional[Dict] = None
    ctx: Dict[str, Dict] = {}
    model = None
    dirs = discover_run_dirs([group])
    dirs += discover_run_dirs(references) if references else []
    for d in dirs:
        beh = load_json(d, "behavioral.json")
        if beh is None or beh["metadata"].get("game_type") != "public_goods":
            continue
        meta, block = beh["metadata"], beh["opponents"][0]
        model = model or _model(meta)
        if _model(meta) != model:
            continue
        m = CKPT_RE.search(meta.get("checkpoint") or "base")
        value = meta["moral_value"]
        if m is None:
            if value == "none":
                base = base or block
            elif not context or value in context:
                ctx.setdefault(value, block)
        elif value == "none":
            run = runs.setdefault(m.group(1), Run(m.group(1), model))
            run.points.setdefault(int(m.group(2)), block)
    for run in runs.values():
        run.base, run.context = base, ctx
    return [runs[k] for k in sorted(runs)]


# --------------------------------------------------------------- drawing
#
# One layout system for the three transfer figures, so they stack as rows
# of a report page: every figure is WIDTHS["wide"] (= \textwidth) across,
# panels share the run colour (RUN_COLORS by run order, identical in all
# three), base is dashed grey, in-context rows dotted violet (ACCENT),
# labels are direct (inside a blank right margin of each panel), and the
# only legend is a one-line footnote naming the two conventions.
#
#   pooled   P(C) vs training step, one axes           height 2.1 in
#   final    last checkpoint per run, own C solid /
#            own D dashed -- the screen's Figure-1 form  height 2.0 in
#   curves   every checkpoint, rows = own move          height 3.8 in

OWN_STYLE = {"C": dict(ls="-", marker="o"), "D": dict(ls="--", marker="s")}
LABEL_MARGIN = 1.15          # k-axis units reserved right of the last k


def curve(block: Dict, own: str) -> List[float]:
    cv = block["pgg"]["cond_contribution_curve"][own]
    return [100 * cv[str(k)]["p_C"] for k in range(len(cv))]


def run_color(j: int) -> str:
    return RUN_COLORS[j % len(RUN_COLORS)]


def shade(color: str, i: int, n: int) -> Tuple[float, float, float]:
    """Light-to-full tint of a run colour by checkpoint order."""
    r, g, b = mcolors.to_rgb(color)
    w = 0.6 * (1 - (i + 1) / n)
    return (r + (1 - r) * w, g + (1 - g) * w, b + (1 - b) * w)


def title(run: Run) -> str:
    return f"{run.model}\n{run.channel}"


def _k_axes(ax, ks, xlabel: bool) -> None:
    ax.set_ylim(-3, 103)
    ax.set_xlim(-0.15, ks[-1] + LABEL_MARGIN)
    ax.set_xticks(ks)
    clean_axes(ax)
    if xlabel:
        ax.set_xlabel("$k_O$ (others contributing)", fontsize=7.5)


def _footnote(fig, runs: List[Run], base_style="dashed grey",
              ctx_style="dotted violet", extra: str = "") -> None:
    ctx = sorted({v for r in runs for v in r.context})
    parts = [f"base = untrained ({base_style})"]
    if ctx:
        parts.append("+ctx = base with " + " / ".join(f"`{v}`" for v in ctx)
                     + f" in context ({ctx_style})")
    if extra:
        parts.append(extra)
    footnote(fig, parts)


def _references(ax, run: Run, own: str, labels: list, ks_out: list) -> None:
    """Draw base and in-context curves for one own move; collect labels."""
    if run.base is not None:
        ys = curve(run.base, own)
        ks_out[:] = list(range(len(ys)))
        ax.plot(ks_out, ys, **REF_BASE)
        labels.append((ys[-1], "base", INK_MUTED))
    for block in run.context.values():
        ys = curve(block, own)
        ks_out[:] = list(range(len(ys)))
        ax.plot(ks_out, ys, **REF_CONTEXT)
        labels.append((ys[-1], "+ctx", ACCENT))


def fig_pooled(runs: List[Run], out_dir: Path, name: str) -> List[Path]:
    fig, ax = plt.subplots(figsize=(WIDTHS["wide"], 2.1))
    xmax = max(max(r.steps) for r in runs)
    labels, base_labels, seen = [], [], set()
    for j, run in enumerate(runs):
        color = run_color(j)
        xs = ([0] if run.base is not None else []) + run.steps
        ys = (([100 * run.base["cooperation_rate"]] if run.base is not None else [])
              + [100 * run.points[s]["cooperation_rate"] for s in run.steps])
        ax.plot(xs, ys, color=color, marker="o", ms=3.2, lw=1.4)
        labels.append((ys[-1], f"{run.model} {run.channel}", color))
        if run.base is not None and run.model not in seen:
            y = 100 * run.base["cooperation_rate"]
            ax.plot([0], [y], "o", ms=5, mfc="white", mec=INK, zorder=5)
            base_labels.append((y, f"{run.model} base", INK))
        for value, block in run.context.items():
            if (run.model, value) in seen:
                continue
            seen.add((run.model, value))
            y = 100 * block["cooperation_rate"]
            ax.plot([0, xmax], [y, y], ls=":", lw=1, color=ACCENT)
            labels.append((y, f"{run.model} + {value} in context", ACCENT))
        seen.add(run.model)
    _labels(ax, base_labels, 0, min_gap=5.0, fontsize=6.5)
    _labels(ax, labels, xmax, min_gap=5.5, fontsize=7)
    ax.set_xlabel("training step", fontsize=7.5)
    ax.set_ylabel("pooled P(C) (%)", fontsize=7.5)
    ax.set_ylim(-3, 103)
    ax.set_xlim(-4, xmax * 1.55)          # right margin holds the labels
    ax.set_xticks([x for x in ax.get_xticks() if 0 <= x <= xmax])
    clean_axes(ax)
    fig.subplots_adjust(left=0.09, right=0.99, top=0.97, bottom=0.2)
    return save(fig, out_dir / "figures", f"transfer_pooled_{name}")


def fig_final(runs: List[Run], out_dir: Path, name: str) -> List[Path]:
    """The screen's Figure-1 form: one panel per run at its LAST evaluated
    checkpoint, own previous move C solid / D dashed, base and in-context
    in the same two line styles."""
    ncols = len(runs)
    fig, axes = plt.subplots(1, ncols, sharey=True, squeeze=False,
                             figsize=(WIDTHS["wide"], 2.0),
                             gridspec_kw={"wspace": 0.1})
    for j, run in enumerate(runs):
        ax, color, last = axes[0][j], run_color(j), run.steps[-1]
        labels, ks = [], []
        for own in OWN:
            if run.base is not None:
                ys = curve(run.base, own); ks = list(range(len(ys)))
                ax.plot(ks, ys, color=INK_MUTED, lw=1.0, ms=2.4, **OWN_STYLE[own])
            for block in run.context.values():
                ys = curve(block, own); ks = list(range(len(ys)))
                ax.plot(ks, ys, color=ACCENT, lw=1.0, ms=2.4, **OWN_STYLE[own])
            ys = curve(run.points[last], own); ks = list(range(len(ys)))
            ax.plot(ks, ys, color=color, lw=1.5, ms=3.0, **OWN_STYLE[own])
            labels.append((ys[-1], f"s{last} {own}$_A$", color))
        if run.base is not None:
            labels.append((curve(run.base, "C")[-1], "base", INK_MUTED))
        if run.context:
            labels.append((curve(next(iter(run.context.values())), "C")[-1],
                           "+ctx", ACCENT))
        _labels(ax, labels, ks[-1], min_gap=7.0)
        _k_axes(ax, ks, xlabel=True)
        ax.set_title(title(run), fontsize=8)
        if j == 0:
            ax.set_ylabel("P(C) (%)", fontsize=7.5)
    _footnote(fig, runs, "grey", "violet", "solid = own previous move C, dashed = D")
    fig.subplots_adjust(left=0.07, right=0.99, top=0.8, bottom=0.3)
    return save(fig, out_dir / "figures", f"transfer_final_{name}")


def fig_curves(runs: List[Run], out_dir: Path, name: str) -> List[Path]:
    """Every checkpoint: rows = own previous move, columns = runs, one line
    per checkpoint shaded light-to-full by step."""
    ncols = len(runs)
    fig, axes = plt.subplots(2, ncols, sharey=True, sharex=True, squeeze=False,
                             figsize=(WIDTHS["wide"], 3.8),
                             gridspec_kw={"hspace": 0.25, "wspace": 0.1})
    for j, run in enumerate(runs):
        color = run_color(j)
        for i, own in enumerate(OWN):
            ax, labels, ks = axes[i][j], [], []
            _references(ax, run, own, labels, ks)
            for s_idx, step in enumerate(run.steps):
                ys = curve(run.points[step], own); ks = list(range(len(ys)))
                c = shade(color, s_idx, len(run.steps))
                ax.plot(ks, ys, color=c, marker="o", ms=2.6, lw=1.3)
                labels.append((ys[-1], f"s{step}", c))
            _labels(ax, labels, ks[-1], min_gap=7.0)
            _k_axes(ax, ks, xlabel=(i == 1))
            if i == 0:
                ax.set_title(title(run), fontsize=8)
            if j == 0:
                ax.set_ylabel(f"own prev {own}\nP(C) (%)", fontsize=7.5)
    _footnote(fig, runs)
    fig.subplots_adjust(left=0.08, right=0.99, top=0.9, bottom=0.15)
    return save(fig, out_dir / "figures", f"transfer_curves_{name}")
