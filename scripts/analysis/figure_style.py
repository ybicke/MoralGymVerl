"""Shared matplotlib style for report figures (make_figures.py).

One place for fonts, sizes, and the state color mapping so every figure
in the LaTeX report reads as one system. Categorical colors are slots
1-4 of the validated reference palette (light mode, white surface;
adjacent-pair CVD dE >= 9.1, normal-vision dE >= 22.9). The aqua and
yellow slots sit below 3:1 contrast on white, so every figure direct-
labels its lines/marks -- do not rely on color alone.

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


def save(fig, out_dir, name: str) -> list:
    """Write <name>.pdf (for LaTeX) + <name>.png (for quick viewing)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("pdf", "png"):
        p = out_dir / f"{name}.{ext}"
        fig.savefig(p, bbox_inches="tight", pad_inches=0.02)
        paths.append(p)
    plt.close(fig)
    return paths
