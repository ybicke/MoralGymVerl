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
    ACCENT, INK, INK_MUTED, RUN_COLORS, WIDTHS, clean_axes, save,
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

def curve(block: Dict, own: str) -> List[float]:
    cv = block["pgg"]["cond_contribution_curve"][own]
    return [100 * cv[str(k)]["p_C"] for k in range(len(cv))]


def shade(color: str, i: int, n: int) -> Tuple[float, float, float]:
    """Light-to-full tint of a run colour by checkpoint order."""
    r, g, b = mcolors.to_rgb(color)
    w = 0.6 * (1 - (i + 1) / n)
    return (r + (1 - r) * w, g + (1 - g) * w, b + (1 - b) * w)


def _labels(ax, entries, x, min_gap=6.0, fontsize=7, clip=True):
    """Labels right of anchor x, nudged apart so they never overprint; a
    label sits at its line's height when there is room, else it is pushed
    up in order. Kept inside the axes (the panel reserves a blank margin)
    so a column never writes into its neighbour."""
    entries = sorted(entries, key=lambda e: e[0])
    ys = [e[0] for e in entries]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + min_gap)
    over = ys[-1] - 100 if ys else 0
    if over > 0:                       # slide the stack down if it overflows
        ys = [y - over for y in ys]
    for (y0, text, color), y in zip(entries, ys):
        ax.annotate(text, (x, y), xytext=(3, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=fontsize, color=color,
                    annotation_clip=clip)


def fig_curves(runs: List[Run], out_dir: Path, name: str) -> List[Path]:
    ncols = len(runs)
    fig, axes = plt.subplots(
        2, ncols, sharey=True, sharex=True, squeeze=False,
            figsize=(1.9 * ncols + 0.6, 4.1),
        gridspec_kw={"hspace": 0.28, "wspace": 0.1})
    for j, run in enumerate(runs):
        color = RUN_COLORS[j % len(RUN_COLORS)]
        ks = None
        for i, own in enumerate(OWN):
            ax = axes[i][j]
            labels = []
            if run.base is not None:
                ys = curve(run.base, own)
                ks = list(range(len(ys)))
                ax.plot(ks, ys, color=INK_MUTED, ls="--", lw=1.1)
                labels.append((ys[-1], "base", INK_MUTED))
            for value, block in run.context.items():
                ys = curve(block, own)
                ks = list(range(len(ys)))
                ax.plot(ks, ys, color=ACCENT, ls=":", lw=1.1)
                labels.append((ys[-1], "+ctx", ACCENT))
            steps = run.steps
            for s_idx, step in enumerate(steps):
                ys = curve(run.points[step], own)
                ks = list(range(len(ys)))
                c = shade(color, s_idx, len(steps))
                ax.plot(ks, ys, color=c, marker="o", ms=2.8, lw=1.3)
                labels.append((ys[-1], f"s{step}", c))
            _labels(ax, labels, ks[-1], min_gap=7.0)
            ax.set_ylim(-3, 103)
            ax.set_xlim(-0.15, ks[-1] + 1.05)   # blank margin for the labels
            ax.set_xticks(ks)
            clean_axes(ax)
            if i == 0:
                ax.set_title(f"{run.model}\n{run.channel}", fontsize=8)
            if i == 1:
                ax.set_xlabel("$k_O$ (others contributing)", fontsize=7.5)
            if j == 0:
                ax.set_ylabel(f"own prev {own}\nP(C) %", fontsize=7.5)
    ctx_values = sorted({v for r in runs for v in r.context})
    fig.subplots_adjust(bottom=0.17 if ctx_values else 0.13)
    if ctx_values:
        fig.text(0.5, 0.01, "+ctx = base with " + " / ".join(f"`{v}`" for v in ctx_values)
                 + " in context (dotted); base = untrained (dashed)",
                 ha="center", fontsize=7, color=INK_MUTED)
    return save(fig, out_dir / "figures", f"transfer_curves_{name}")


def fig_pooled(runs: List[Run], out_dir: Path, name: str) -> List[Path]:
    fig, ax = plt.subplots(figsize=(WIDTHS["column"] + 0.9, 2.4))
    labels = []
    xmax = max(max(r.steps) for r in runs)
    for j, run in enumerate(runs):
        color = RUN_COLORS[j % len(RUN_COLORS)]
        xs = ([0] if run.base is not None else []) + run.steps
        ys = (([100 * run.base["cooperation_rate"]] if run.base is not None else [])
              + [100 * run.points[s]["cooperation_rate"] for s in run.steps])
        ax.plot(xs, ys, color=color, marker="o", ms=3.2, lw=1.4)
        labels.append((ys[-1], f"{run.model} {run.channel}", color))
    seen = set()
    base_labels = []
    for run in runs:
        if run.base is not None and run.model not in seen:
            y = 100 * run.base["cooperation_rate"]
            ax.plot([0], [y], "o", ms=5, mfc="white", mec=INK, zorder=5)
            base_labels.append((y, f"{run.model} base", INK))
        for value, block in run.context.items():
            key = (run.model, value)
            if key in seen:
                continue
            seen.add(key)
            y = 100 * block["cooperation_rate"]
            ax.axhline(y, ls=":", lw=1, color=ACCENT)
            ax.annotate(f"{run.model} + {value} in context", (0, y),
                        xytext=(3, 3), textcoords="offset points",
                        fontsize=6.5, color=ACCENT)
        seen.add(run.model)
    _labels(ax, base_labels, 0, min_gap=5.0, fontsize=6.5, clip=False)
    _labels(ax, labels, xmax, min_gap=5.0, fontsize=7, clip=False)
    ax.set_xlabel("training step")
    ax.set_ylabel("pooled P(C) %")
    ax.set_ylim(-3, 103)
    ax.set_xlim(-5, xmax * 1.02)
    clean_axes(ax)
    fig.subplots_adjust(right=0.62)
    return save(fig, out_dir / "figures", f"transfer_pooled_{name}")
