#!/usr/bin/env python3.11
"""Results document for checkpoint evals on the public-goods game.

The transfer question: weights trained on single-round 2-player PD play
the 4-player binary public-goods game with NO moral text in the prompt.
Table T1 puts every policy in the PGG screen's own Table-1 frame (the
conditional contribution curve, C_A | D_A per k_O, decision_full, fixed
presentation, balanced states) so the rows read against each other:

    base, no context            the untrained policy (a `checkpoint: base`
                                cell of the group, or a screen reference)
    <run>, step N               one panel per training run in the group
    base + <value> in context   the screen's in-context rows (--reference)

Two columns the screen does not carry: pooled P(C) -- the headline for
"is the trained policy more cooperative than the base" -- and the k-slope
within own_prev, the N-player analogue of D_opp (does it respond to how
many others contributed). Then the trace measures on THESE traces
(normative vocabulary, verbatim recitation of --principle), pooled per
policy, and example traces per policy x state.

    /usr/bin/python3.11 scripts/analysis/make_results.py \\
        eval_results/post_training/qwen3_8b/pgg/transfer \\
        --reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from eval_cells import check_comparability, discover_run_dirs  # noqa: E402
from measures import normative_hit, principle_ngrams, principle_overlap  # noqa: E402
from results_doc import (  # noqa: E402
    MISSING, VALUES, Cell, Table, exemplars_section, pct,
    prompt_design_section, to_latex, to_markdown,
)
from specs.post_training import checkpoint_of  # noqa: E402
from specs.screen_pgg import _trace_tags, curve_rows, game_note, parse_cell  # noqa: E402
from trace_measures import NORMATIVE_VOCAB, OVERLAP_WORDS  # noqa: E402
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402

VALUE_NAMES = dict(VALUES)
BASE = "base, no context"


# ------------------------------------------------------------- policies

class Policy:
    """One row of Table T1: a PGG cell plus where it sits in the table."""
    __slots__ = ("label", "panel", "run", "step", "cell")

    def __init__(self, label, panel, run, step, cell):
        self.label, self.panel, self.run, self.step, self.cell = (
            label, panel, run, step, cell)


def channel(run: str) -> str:
    """'qwen3_8b_grpo_pd_deon_tft_200' -> 'GRPO deon' (docs/naming.md
    fields 3 and 5; the full run name stays in the panel title)."""
    f = run.split("_")
    return f"{f[2].upper()} {f[4]}" if len(f) >= 6 else run


def context_label(value: str) -> str:
    return f"base + `{value}` in context"


def collect(group: Path, references: List[Path],
            context: List[str]) -> List[Policy]:
    """Group cells first (base + trained), then reference rows the group
    does not already hold. First occurrence of a label wins."""
    seen: Dict[str, Policy] = {}

    def add(run_dir: Path, from_reference: bool) -> None:
        cell = parse_cell(run_dir)
        if cell is None:
            return
        run, step = checkpoint_of(cell["meta"])
        value = cell["meta"]["moral_value"]
        if run == "base":
            if value == "none":
                label, panel = BASE, BASE
            elif not context or value in context:
                label = panel = context_label(value)
            else:
                return
        else:
            if from_reference:
                return                      # references contribute base rows only
            label, panel = f"step {step}", f"{channel(run)} — `{run}`"
            label = f"{panel}::{label}"     # unique across runs
        seen.setdefault(label, Policy(label, panel, run, step, cell))

    for run_dir in discover_run_dirs([group]):
        add(run_dir, from_reference=False)
    for run_dir in (discover_run_dirs(references) if references else []):
        add(run_dir, from_reference=True)

    base = [p for p in seen.values() if p.label == BASE]
    trained = sorted((p for p in seen.values() if p.run != "base"),
                     key=lambda p: (p.run, p.step))
    ctx = [p for p in seen.values() if p.run == "base" and p.label != BASE]
    return base + trained + ctx


def panels_of(policies: List[Policy]) -> List[Tuple[str, List[Policy]]]:
    out: List[Tuple[str, List[Policy]]] = []
    for p in policies:
        if out and out[-1][0] == p.panel:
            out[-1][1].append(p)
        else:
            out.append((p.panel, [p]))
    return out


def row_label(p: Policy) -> str:
    return p.label.split("::", 1)[1] if "::" in p.label else p.label


# ---------------------------------------------------------------- table

def transfer_table(policies: List[Policy], group: Path) -> Table:
    curves = {p.label: curve_rows(p.cell) for p in policies}
    n_states = len(next(iter(curves.values()))["C"][0])
    meta = policies[0].cell["meta"]
    per_state = meta["num_episodes"] // (2 * n_states)

    panels = []
    for title, members in panels_of(policies):
        rows = []
        for p in members:
            cv, block = curves[p.label], p.cell["block"]
            row: List[Cell] = []
            for k in range(n_states):
                row += [Cell(pct(cv[own][0][k] / cv[own][1][k]))
                        for own in ("C", "D")]
            row += [Cell(pct(sum(cv[own][0]) / sum(cv[own][1])))
                    for own in ("C", "D")]
            row.append(Cell(pct(block["cooperation_rate"]), bold=True))
            slope = block["pgg"]["k_slope"]
            row += [Cell(f"{100 * slope[own]:+.0f}") for own in ("C", "D")]
            rows.append((row_label(p), row))
        panels.append((title, rows))

    return Table(
        key="transfer",
        title=f"Table T1 — PGG transfer: state-conditioned contribution ({group.parent.parent.name})",
        subtitle=(f"`{meta['representation']}` representation, fixed "
                  "presentation, protocol `single_round` (fabricated "
                  "history, balanced states), NO moral text in the trained "
                  "rows. Each cell: agent's previous move C$_A$ $\\mid$ D$_A$."),
        caption=(
            "Contribution rate (\\%) by fabricated previous round: C$_A$ / "
            "D$_A$ is the agent's own move, $k_O$ how many of the $N-1$ "
            f"others contributed; {per_state} episodes per state (binomial "
            "s.e.\\ $\\leq$7 points). mean pools the $k_O$ states within an "
            "own move; P(C) pools all states -- the balanced design makes it "
            "the cell's overall contribution rate, the transfer headline. "
            "slope = least-squares change in P(C) per unit $k_O$ within an "
            "own move, in points (the N-player analogue of $\\Delta_{opp}$: "
            "positive = contributes more when more others did). Trained "
            "rows are the checkpoints evaluated here; base rows are "
            "untrained cells of the same model under the same protocol."),
        stub="Policy (C$_A$ $\\mid$ D$_A$)",
        col_groups=[(f"P(C $\\mid$ · , $k_O$ = {k})", ["C$_A$", "D$_A$"])
                    for k in range(n_states)]
                   + [("mean", ["C$_A$", "D$_A$"]), (None, ["P(C)"]),
                      ("slope /$k_O$", ["C$_A$", "D$_A$"])],
        panels=panels,
        pair_groups=True,
    )


def trace_table(policies: List[Policy], principle: str) -> Table:
    grams = principle_ngrams(get_moral_value(principle))
    panels = []
    for title, members in panels_of(policies):
        rows = []
        for p in members:
            raws = [r["raw"] for r in p.cell["records"]]
            n = len(raws)
            vocab = sum(normative_hit(t) for t in raws)
            overlap = sum(principle_overlap(t, grams) for t in raws)
            rows.append((row_label(p), [Cell(str(n)), Cell(pct(vocab / n)),
                                        Cell(pct(overlap / n))]))
        panels.append((title, rows))
    return Table(
        key="transfer-traces",
        title="Table T2 — reasoning traces on the transfer cells",
        subtitle="Same traces as Table T1, pooled over the eight states.",
        caption=(
            "normative \\% = share of traces containing at least one of "
            f"{len(NORMATIVE_VOCAB)} reviewed word stems ({', '.join(NORMATIVE_VOCAB)}), "
            "matched on word boundaries; one hit marks the trace. It "
            "measures whether the trace reaches for moral VOCABULARY, not "
            "whether a norm is applied. "
            f"recites \\% = the `{principle}` wording split into words and "
            f"all its {OVERLAP_WORDS}-word n-grams collected; a trace counts "
            f"if it reproduces ANY {OVERLAP_WORDS} consecutive words. "
            "VERBATIM only -- a faithful paraphrase scores 0. The principle "
            "text is in none of these prompts except the in-context rows, "
            "where it is the prompt itself."),
        stub="Policy",
        col_groups=[(None, ["n"]), (None, ["normative \\%"]),
                    (None, ["recites \\%"])],
        panels=panels,
    )


# --------------------------------------------------------------- figure

def transfer_figure(policies: List[Policy], path: Path) -> None:
    """Left: pooled P(C) against training step, one line per run; base at
    step 0 and the in-context rows as dotted reference levels. Right: the
    conditional curve at each run's last evaluated step against base
    (solid = C_A, dashed = D_A). Direct-labelled; colour by run, never the
    only cue."""
    from figure_style import (INK, INK_MUTED, RUN_COLORS, WIDTHS, apply_style,
                              clean_axes, save)
    import matplotlib.pyplot as plt

    apply_style()
    base = next((p for p in policies if p.label == BASE), None)
    ctx = [p for p in policies if p.run == "base" and p.label != BASE]
    runs: Dict[str, List[Policy]] = {}
    for p in policies:
        if p.run != "base":
            runs.setdefault(p.run, []).append(p)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(WIDTHS["wide"], 2.5),
                                   gridspec_kw={"width_ratios": [1.25, 1]})
    for i, (run, ps) in enumerate(runs.items()):
        color = RUN_COLORS[i % len(RUN_COLORS)]
        xs = [0] + [p.step for p in ps] if base else [p.step for p in ps]
        ys = ([base.cell["block"]["cooperation_rate"]] if base else []) \
            + [p.cell["block"]["cooperation_rate"] for p in ps]
        ax1.plot(xs, [100 * y for y in ys], marker="o", ms=3.5, color=color)
        ax1.annotate(channel(run), (xs[-1], 100 * ys[-1]), xytext=(4, 0),
                     textcoords="offset points", fontsize=7.5, color=color,
                     va="center")
    if base:
        ax1.plot([0], [100 * base.cell["block"]["cooperation_rate"]], "o",
                 ms=5, mfc="white", mec=INK, zorder=5)
        ax1.annotate("base", (0, 100 * base.cell["block"]["cooperation_rate"]),
                     xytext=(0, 6), textcoords="offset points", fontsize=7.5,
                     ha="center", color=INK)
    for p in ctx:
        y = 100 * p.cell["block"]["cooperation_rate"]
        ax1.axhline(y, ls=":", lw=1, color=INK_MUTED)
        ax1.annotate(f"+{p.cell['meta']['moral_value']} in context", (0, y),
                     xytext=(2, 3), textcoords="offset points", fontsize=7,
                     color=INK_MUTED)
    ax1.set_xlabel("training step")
    ax1.set_ylabel("pooled P(C) %")
    ax1.set_ylim(0, 100)
    clean_axes(ax1)

    n_states = len(policies[0].cell["block"]["pgg"]["cond_contribution_curve"]["C"])
    ks = list(range(n_states))

    def curve(p: Policy, own: str) -> List[float]:
        cv = p.cell["block"]["pgg"]["cond_contribution_curve"][own]
        return [100 * cv[str(k)]["p_C"] for k in ks]

    style = {"C": dict(ls="-", marker="o"), "D": dict(ls="--", marker="s")}
    if base:
        for own in ("C", "D"):
            ax2.plot(ks, curve(base, own), color=INK_MUTED, ms=3.5, **style[own])
        ax2.annotate("base", (ks[-1], curve(base, "C")[-1]), xytext=(4, 0),
                     textcoords="offset points", fontsize=7, color=INK_MUTED,
                     va="center")
    for i, (run, ps) in enumerate(runs.items()):
        color = RUN_COLORS[i % len(RUN_COLORS)]
        last = ps[-1]
        for own in ("C", "D"):
            ax2.plot(ks, curve(last, own), color=color, ms=3.5, **style[own])
        ax2.annotate(f"{channel(run)} s{last.step}", (ks[-1], curve(last, "C")[-1]),
                     xytext=(4, 0), textcoords="offset points", fontsize=7,
                     color=color, va="center")
    ax2.set_xticks(ks)
    ax2.set_xlabel("$k_O$ (others contributing last round)")
    ax2.set_ylabel("P(C) %")
    ax2.set_ylim(0, 100)
    ax2.plot([], [], color=INK, ls="-", marker="o", ms=3.5, label="own prev C")
    ax2.plot([], [], color=INK, ls="--", marker="s", ms=3.5, label="own prev D")
    ax2.legend(loc="upper left", fontsize=7)
    clean_axes(ax2)
    fig.tight_layout()
    save(fig, path.parent, path.stem)


# --------------------------------------------------------------- header

def header(policies: List[Policy], group: Path) -> str:
    meta = policies[0].cell["meta"]
    model = meta["base_model"].rsplit("/", 1)[-1]
    runs = panels_of([p for p in policies if p.run != "base"])
    lines = [f"# PGG transfer: {group.parent.parent.name} / {group.name}", "",
             f"{model}. Checkpoints trained on single-round 2-player PD, "
             "evaluated on the 4-player binary public-goods game with no "
             "moral text in the prompt: any contribution beyond the base "
             "row is carried by the weights into a game the policy never "
             "played. Runs and evaluated steps:", ""]
    for title, ps in runs:
        lines.append(f"- {title}: steps {', '.join(str(p.step) for p in ps)}")
    lines += ["", "**Reference rows** (untrained cells of the same model, "
              "same protocol):", "",
              "| row | group | cell | job |", "|---|---|---|---|"]
    for p in policies:
        if p.run != "base":
            continue
        rd = p.cell["run_dir"]
        lines.append(f"| {p.label} | `{rd.parent.parent.parent.parent.name}/"
                     f"{rd.parent.parent.parent.name}/{rd.parent.parent.name}` | "
                     f"`{rd.name}` | {p.cell['meta'].get('slurm_job_id', '—')} |")
    parse_fail = max(p.cell["block"]["parse_failure_rate"] for p in policies)
    lines += ["", game_note(policies[0].cell["payoffs"]), "",
              f"Protocol: `{meta['representation']}` representation, fixed "
              f"presentation, `game_description` "
              f"{'on' if meta.get('game_description') else 'off'}, "
              f"T = {meta['eval_temperature']}, "
              f"{meta['eval_max_new_tokens']} max new tokens, seed "
              f"{meta['eval_seed']}, {meta['num_episodes']} episodes per "
              f"cell. Parse-failure rate at worst {100 * parse_fail:.1f}% "
              "across cells. Every table is computed from the traces in "
              "`cells/*/behavioral.responses.jsonl` and checked against the "
              "published `cond_contribution_curve`. Generated by "
              "`scripts/analysis/make_results.py` (LaTeX in `tex/`).",
              "", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------- main

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", action="append", type=Path, default=[],
                        help="PGG screen group(s) holding the base-none and "
                             "in-context cells (repeatable); a group's own "
                             "`checkpoint: base` cells take precedence")
    parser.add_argument("--context", action="append", default=[],
                        help="which in-context reference arms to show "
                             "(repeatable; default: every non-none arm found)")
    parser.add_argument("--principle", default="deontological+repair+generosity",
                        help="wording whose verbatim recitation Table T2 counts")
    parser.add_argument("--exemplars", type=int, default=1,
                        help="example traces per policy x state (0 = none)")


def build(args: argparse.Namespace) -> Path:
    """PGG transfer of PD-trained checkpoints: Table T1 vs base and
    in-context rows, trace measures, example traces."""
    group = args.paths[0]
    if not check_comparability(discover_run_dirs([group])) and not args.allow_mixed:
        raise SystemExit("ERROR: cells differ in an undeclared setting (see "
                         "WARNINGs). --allow-mixed to proceed.")
    policies = collect(group, args.reference, args.context)
    if not any(p.run != "base" for p in policies):
        raise SystemExit(f"{group}: no trained public-goods cells")
    if not any(p.label == BASE for p in policies):
        print("WARNING: no base-none row (add --reference <pgg screen group>)")

    out_dir = args.out or (group / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)

    def emit(table: Table) -> str:
        (tex_dir / f"{table.key.replace('-', '_')}.tex").write_text(to_latex(table))
        return to_markdown(table)

    cells = [p.cell["cell"] for p in policies]
    md = [header(policies, group), prompt_design_section(cells)]
    md.append(emit(transfer_table(policies, group)))
    fig_path = out_dir / "figures" / "transfer.png"
    transfer_figure(policies, fig_path)
    print(f"saved -> {fig_path}")
    md.append("\n".join([
        "### Figure 1 — transfer", "",
        f"![pooled P(C) by step; conditional curves]({fig_path.relative_to(out_dir)})",
        "", "Left: pooled P(C) against training step per run, base at step 0, "
        "in-context rows dotted. Right: the conditional contribution curve at "
        "each run's last evaluated step against base (solid = own previous "
        "move C, dashed = D).", "", ""]))
    md.append(emit(trace_table(policies, args.principle)))
    if args.exemplars:
        label_of = {p.cell["run_dir"]: p.label for p in policies}
        md.append(exemplars_section(
            cells, per_state=args.exemplars, tags=_trace_tags,
            arm_order=[p.label for p in policies],
            arm_of=lambda c: label_of[c.run_dir]))
    path = out_dir / f"results_{group.name}.md"
    path.write_text("\n".join(md))
    print(f"saved -> {path}")
    return path
