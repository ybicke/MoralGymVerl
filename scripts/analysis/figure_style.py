"""Shared matplotlib style for report figures -- the STYLE CONTRACT.

Every figure in the LaTeX report (make_figures.py, transfer_figures.py,
and any future module) draws from here, so the set reads as one system
and is publication-ready without per-figure tuning:

- WIDTH: every figure is WIDTHS["wide"] (= \textwidth) across and
  stacks as a report row; single-panel exceptions use a fraction of it.
- COLOR SEMANTICS: color encodes the panel's comparison unit --
  STATE_COLORS when lines compare fabricated states within one run,
  RUN_COLORS (fixed order by run position) when lines compare runs.
  Checkpoint progression within a run = light-to-full tint (shade()).
- REFERENCES: the untrained base is ALWAYS REF_BASE (dashed grey), a
  principle-in-context cell ALWAYS REF_CONTEXT (dotted violet).
- LABELS: direct labels via direct_labels() in a reserved margin;
  a legend only where direct labels cannot carry identity. Conventions
  that need naming go into one footnote() line, never a second legend.

Categorical colors are slots of the validated reference palette (light
mode, white surface; adjacent-pair CVD dE >= 9.1, normal-vision dE >=
22.9). The aqua and yellow slots sit below 3:1 contrast on white, so
direct labels are mandatory -- never rely on color alone.

Login node: /usr/bin/python3.11 (matplotlib in ~/.local).
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Fabricated previous-round state -> fixed hue (never re-assigned when a
# figure drops a state; color follows the entity).
STATE_COLORS = {
    "CC": "#2a78d6",  # blue
    "CD": "#eb6834",  # orange
    "DC": "#1baf7a",  # aqua
    "DD": "#eda100",  # yellow
}
ACCENT = "#4a3aa7"    # violet: reserved for non-state annotations

# Cross-run figures color RUNS, not states: fixed categorical order
# (validated reference palette), assigned by position in the run list --
# a run keeps its slot even when others are dropped from a figure.
RUN_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
              "#008300", "#4a3aa7", "#e34948"]
GRID = "#d9d8d2"
INK = "#1a1a19"
INK_MUTED = "#6b6a63"

# Reference-line grammar, identical in every figure.
REF_BASE = {"color": INK_MUTED, "ls": "--", "lw": 1.0}     # untrained base
REF_CONTEXT = {"color": ACCENT, "ls": ":", "lw": 1.0}      # principle in ctx

# TeX-ready labels, subscripts matching the results docs' convention.
STATE_TEX = {
    "CC": r"$\mathrm{C_A C_O}$",
    "CD": r"$\mathrm{C_A D_O}$",
    "DC": r"$\mathrm{D_A C_O}$",
    "DD": r"$\mathrm{D_A D_O}$",
}

# Figure widths in inches: 'wide' ~= \textwidth of an a4 article,
# 'column' ~= \columnwidth of a two-column layout. Render at final size
# so font sizes in the PDF are honest.
WIDTHS = {"wide": 6.0, "column": 3.35}


def apply_style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 8.5,
        "axes.titlesize": 9,
        "axes.labelsize": 8.5,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 7.5,
        "axes.edgecolor": INK_MUTED,
        "axes.linewidth": 0.6,
        "axes.labelcolor": INK,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "xtick.labelcolor": INK,
        "ytick.labelcolor": INK,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.5,
        "axes.axisbelow": True,
        "legend.frameon": False,
        "lines.linewidth": 1.6,
        "figure.dpi": 100,
        "savefig.dpi": 300,
        "pdf.fonttype": 42,   # embed TrueType, editable in the PDF
    })


def clean_axes(ax) -> None:
    """Recessive frame: keep left/bottom spines only, y-grid only."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y")
    ax.grid(axis="x", visible=False)


def direct_labels(ax, entries, x, min_gap=6.0, fontsize=7.5, clip=True):
    """Labels right of anchor x, nudged apart so they never overprint; a
    label sits at its line's height when there is room, else it is pushed
    up in order, and the stack slides down if it overflows the axes.
    entries: [(y, text, color)] in data coords."""
    entries = sorted(entries, key=lambda e: e[0])
    ys = [e[0] for e in entries]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + min_gap)
    over = ys[-1] - 100 if ys else 0
    if over > 0:
        ys = [y - over for y in ys]
    for (y0, text, color), y in zip(entries, ys):
        ax.annotate(text, (x, y), xytext=(3, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=fontsize, color=color,
                    annotation_clip=clip)


def footnote(fig, parts: list) -> None:
    """One muted line naming the figure's drawing conventions."""
    fig.text(0.5, 0.01, "; ".join(parts), ha="center", fontsize=7,
             color=INK_MUTED)


def save(fig, out_dir, name: str) -> list:
    """Write the quick-look <name>.png at the top level and the
    canonical vector PDF (what Overleaf includes) under pdf/."""
    (out_dir / "pdf").mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{name}.png", out_dir / "pdf" / f"{name}.pdf"]
    for p in paths:
        fig.savefig(p, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return paths
