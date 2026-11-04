"""Seed-replicate figures for held-out-game transfer (PGG).

The per-run figures in transfer_figures.py key on one run name, so three
seeds of one config appear as three runs. Here cells are pooled by
CHANNEL (algo + arm, the run name without model, steps and seed) and
drawn as mean with a min-max band over seeds. Base and in-context rows
come from the same groups (checkpoint: base cells of the given model).

  seed-grid  left: pooled P(C) at one step, bars = base / base+ctx /
             channels, dots = seeds; right: P(C) vs k_O for own previous
             move C and D, band over seeds.
  seed-multi multi-round P(C) per round, one panel per co-player group,
             band over seeds, base dashed, base+ctx dotted. With
             --split-thinking, cells evaluated with the thinking block on
             and off form separate channels ("think" / "no-think"), each
             with its own base+ctx reference.

Same data layer and style as the rest of make_figures.py.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matplotlib.pyplot as plt  # noqa: E402
from eval_cells import discover_run_dirs, load_json  # noqa: E402
from figure_style import (  # noqa: E402
    ACCENT, CHANNEL_COLORS, INK_MUTED, REF_BASE, REF_CONTEXT, RUN_COLORS, WIDTHS,
    clean_axes, direct_labels, save,
)
from transfer_figures import OWN, OWN_STYLE, _k_axes, curve  # noqa: E402

CKPT_RE = re.compile(r"/([^/]+?)(?:_seed(\d+))?/global_step_(\d+)/")


class Cell:
    """One PGG cell: its channel, seed, step and the opponents blocks."""

    def __init__(self, meta: Dict, opponents: List[Dict]):
        self.model = meta["base_model"].rsplit("/", 1)[-1]
        self.value = meta["moral_value"]
        self.protocol = meta["protocol"]
        self.representation = meta.get("representation")
        self.thinking = bool(meta.get("enable_thinking"))
        self.tag = ""                      # set by load_cells(split_thinking)
        self.blocks = {o["opponent"]: o for o in opponents}
        self.first = opponents[0]
        m = CKPT_RE.search(meta.get("checkpoint") or "")
        if m is None:                      # untrained model
            self.channel, self.seed, self.step = "base", None, None
        else:
            f = m.group(1).split("_")
            self.channel = f"{f[2].upper()} {f[4]}" if len(f) >= 6 else f[0]
            self.seed = int(m.group(2)) if m.group(2) else meta.get("training_seed")
            self.step = int(m.group(3))

    @property
    def algo(self) -> str:
        return self.channel.split()[0]


def load_cells(groups: List[Path], protocol: str, model: Optional[str] = None,
               seed: Optional[int] = None, channels: Optional[List[str]] = None,
               split_thinking: bool = False) -> List[Cell]:
    """PGG cells of one protocol; --seed keeps one training seed (reference
    rows have none and are always kept); --channel keeps the named algos
    (GRPO, SDPO); --split-thinking tags every cell "think" / "no-think"
    by its enable_thinking flag so the two evaluation modes are drawn
    as separate channels."""
    cells = []
    for d in discover_run_dirs(groups):
        beh = load_json(d, "behavioral.json")
        if beh is None or beh["metadata"].get("game_type") != "public_goods":
            continue
        if beh["metadata"].get("protocol") != protocol:
            continue
        c = Cell(beh["metadata"], beh["opponents"])
        if model and c.model != model:
            continue
        if seed is not None and c.channel != "base" and c.seed != seed:
            continue
        if channels and c.channel != "base" and c.algo not in channels:
            continue
        if split_thinking:
            c.tag = "think" if c.thinking else "no-think"
            if c.channel != "base":
                c.channel = f"{c.channel} {c.tag}"
        cells.append(c)
    if not cells:
        raise SystemExit(f"no {protocol} PGG cells under the --group dirs")
    return cells


def split(cells: List[Cell], steps: List[int], principle: str):
    """-> {tag: base cell}, {(moral_value, tag): ctx cell},
    {(channel, step): [seed cells]}; tag is "" unless the cells were
    loaded with split_thinking. Reference rows must share the
    checkpoints' presentation."""
    runs = defaultdict(dict)
    for c in cells:
        if c.channel != "base" and c.value == "none" and c.step in steps:
            runs[(c.channel, c.step)].setdefault(c.seed, c)   # one cell per seed
    if not runs:
        raise SystemExit(f"no checkpoint cells at steps {steps}")
    rep = next(iter(runs.values()))[next(iter(next(iter(runs.values()))))].representation
    base, ctx = {}, {}
    for c in cells:
        if c.channel == "base" and c.representation == rep:
            if c.value == "none":
                base.setdefault(c.tag, c)
            elif c.value == principle:
                ctx.setdefault((c.value, c.tag), c)
    return base, ctx, {k: list(v.values()) for k, v in runs.items()}


def band(ax, xs, series: List[List[float]], color, **line):
    """Mean line with a min-max band over the replicate series."""
    n = len(series)
    mean = [sum(s[i] for s in series) / n for i in range(len(xs))]
    if n > 1:
        ax.fill_between(xs, [min(s[i] for s in series) for i in range(len(xs))],
                        [max(s[i] for s in series) for i in range(len(xs))],
                        color=color, alpha=0.18, lw=0)
    ax.plot(xs, mean, color=color, **line)
    return mean


def _channels(runs: Dict) -> List:
    """GRPO first, then SDPO; steps ascending within a channel."""
    return sorted(runs, key=lambda k: (k[0].split()[0] != "GRPO", k))


def _style(key, runs) -> tuple:
    """Colour by channel; several steps of one channel shade light-to-full."""
    ch, step = key
    color = CHANNEL_COLORS.get(ch.split()[0], RUN_COLORS[1])
    steps = sorted(s for c, s in runs if c == ch)
    label = ch.split()[0] + (f" s{step}" if len(steps) > 1 else "")
    from transfer_figures import shade
    if len(steps) > 1:
        color = shade(color, steps.index(step), len(steps))
    if ch.endswith(" no-think"):
        color, label = shade(color, 0, 2), label + " no-think"
    elif ch.endswith(" think"):
        label += " think"
    return color, label


def _end(ys: List[float]) -> float:
    """Label height: the last point, ties broken by the curve's mean so
    lines that meet at the right edge keep their vertical order."""
    return ys[-1] + 1e-3 * sum(ys) / len(ys)


def fig_seed_grid(cells: List[Cell], steps: List[int], principle: str,
                  out_dir: Path, name: str, title: Optional[str] = None) -> List[Path]:
    """The transfer_final form, pooled over seeds: one panel per model,
    P(C) vs k_O, own previous move C solid / D dashed, band = min-max."""
    base, ctx, runs = split(cells, steps, principle)
    base = next(iter(base.values()), None)
    fig, ax = plt.subplots(figsize=(WIDTHS["column"], 2.3))
    labels, ks = [], []
    for own in OWN:
        if base:
            ys = curve(base.first, own); ks = list(range(len(ys)))
            ax.plot(ks, ys, color=INK_MUTED, lw=0.9, ms=2.0, **OWN_STYLE[own])
        for c in ctx.values():
            ys = curve(c.first, own); ks = list(range(len(ys)))
            ax.plot(ks, ys, color=ACCENT, lw=0.9, ms=2.0, **OWN_STYLE[own])
            labels.append((_end(ys), f"+ctx {own}$_A$", ACCENT))
        for key in _channels(runs):
            color, label = _style(key, runs)
            series = [curve(c.first, own) for c in runs[key]]
            ks = list(range(len(series[0])))
            mean = band(ax, ks, series, color, lw=1.2, ms=2.6, **OWN_STYLE[own])
            labels.append((_end(mean), f"{label} {own}$_A$", color))
    if base:
        ends = [curve(base.first, own)[-1] for own in OWN]
        labels.append((sum(ends) / len(ends), "base", INK_MUTED))
    direct_labels(ax, labels, ks[-1], min_gap=8.0, fontsize=7)
    _k_axes(ax, ks, xlabel=True)
    ax.set_ylabel("P(C) (%)", fontsize=7.5)
    ax.set_title(title or f"{next(iter(cells)).model}, step {'/'.join(map(str, steps))}", fontsize=8)
    fig.subplots_adjust(left=0.13, right=0.99, top=0.9, bottom=0.2)
    return save(fig, out_dir / "figures", f"transfer_final_{name}")


def fig_seed_multi(cells: List[Cell], steps: List[int], principle: str,
                   out_dir: Path, name: str, title: Optional[str] = None,
                   legend: bool = False) -> List[Path]:
    """--legend: one shared legend under the panels instead of direct
    labels at the right edge (which cost each panel a label column)."""
    base, ctx, runs = split(cells, steps, principle)
    opps = list(next(iter(list(base.values()) + list(ctx.values()))).blocks)
    from transfer_figures import shade
    n_rounds = max(int(k.split("_")[1]) for c in cells
                   for b in c.blocks.values() for k in (b.get("per_round") or {}))
    xs = list(range(1, n_rounds + 1))
    per_round = lambda c, opp: [100 * c.blocks[opp]["per_round"][f"round_{r}"]["p_C"] for r in xs]
    fig, axes = plt.subplots(1, len(opps), sharey=True, squeeze=False,
                             figsize=(WIDTHS["wide"], 2.2), gridspec_kw={"wspace": 0.12})
    handles = {}                           # label -> line, for the shared legend
    for j, opp in enumerate(opps):
        ax, labels = axes[0][j], []
        for i, (tag, c) in enumerate(base.items()):
            ys = per_round(c, opp); ax.plot(xs, ys, **REF_BASE)
            if i == 0:                     # one label: both modes sit at the floor
                labels.append((ys[-1], "base", INK_MUTED))
                handles.setdefault("base", ax.lines[-1])
        for (_, tag), c in ctx.items():
            style = dict(REF_CONTEXT)
            if tag == "no-think":
                style["color"] = shade(ACCENT, 0, 2)
            ys = per_round(c, opp); ax.plot(xs, ys, **style)
            label = f"+ctx {tag}" if tag else "base+ctx"
            labels.append((ys[-1], label, style["color"]))
            handles.setdefault(label, ax.lines[-1])
        for key in _channels(runs):
            color, label = _style(key, runs)
            mean = band(ax, xs, [per_round(c, opp) for c in runs[key]], color,
                        marker="o", ms=3, lw=1.4)
            labels.append((mean[-1], label, color))
            handles.setdefault(label, ax.lines[-1])
        if legend:
            ax.set_xlim(0.8, n_rounds + 0.3)
        else:
            direct_labels(ax, labels, xs[-1], min_gap=8.0, fontsize=7)
            split_tags = any(tag for tag in base) or any(tag for _, tag in ctx)
            ax.set_xlim(0.8, n_rounds + (2.6 if split_tags else 1.9))
        ax.set_xticks(xs)
        ax.set_ylim(-3, 103)
        ax.set_title(f"vs {opp.replace('_', ' ')}", fontsize=8)
        ax.set_xlabel("round", fontsize=7.5)
        if j == 0:
            ax.set_ylabel("P(C) (%)", fontsize=7.5)
        clean_axes(ax)
    if title:
        fig.suptitle(title, fontsize=8, y=0.995)
    if legend:
        rank = lambda k: (not k.startswith(("GRPO", "SDPO")), k.startswith("base"),
                          k.endswith("no-think"), k)
        order = sorted(handles, key=rank)   # runs, +ctx, base; think before no-think
        fig.legend([handles[k] for k in order], order, loc="lower center",
                   ncol=len(order), fontsize=7, frameon=False, handlelength=2.2,
                   columnspacing=1.4, bbox_to_anchor=(0.5, 0.0))
        fig.subplots_adjust(left=0.07, right=0.99, top=0.88, bottom=0.33)
    else:
        fig.subplots_adjust(left=0.07, right=0.99, top=0.88, bottom=0.2)
    return save(fig, out_dir / "figures", f"multi_round_{name}")
