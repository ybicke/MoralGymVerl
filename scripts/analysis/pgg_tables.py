#!/usr/bin/env python3.11
"""Publication tables (booktabs LaTeX + Markdown) for the public-goods
single-turn screen -- the PGG counterpart to publication_tables.py, which
covers only the three 2x2 games.

The screen fabricates a previous round (own_prev, k_prev) and asks for
one decision, so the evidence is a CONDITIONAL CONTRIBUTION CURVE: how
often the agent contributes at each k, holding its own previous move
fixed. Table 1 is that curve and every other table is subordinate to it:

    contribution        Table 1: P(C | own_prev, k) in the 2x2 Table 1
                        layout, C_A | D_A paired per cell; Figure 1 plots
                        the same curves
    label_inversion     Table 2: which label the trace calls exploitation,
                        cross-tabbed against the decision -- why the
                        deontological D_A curve sits low and ramps
    group_arithmetic    Table 3: which group total the trace computes,
                        likewise -- why the utilitarian curve is null

NOT TABULATED. group_efficiency, sucker/free-ride rates and the regrets
are exact functions of Table 1's eight rates under balanced states
(regret_deon is literally (N-1) x free-ride); they stay in behavioral.json.

TRACE DETECTORS (Tables 2-3). A curve is evidence about a principle only
if the model read the game the principle needs (docs/pgg_design.md
s9.3-9.4), so three misreads are measured as regexes over the traces:
club-good wording (threshold provision), the fixed-others group total
(the utilitarian arithmetic done with everyone else's payoff held at its
observed value, which reverses the welfare ranking at every k), and label
inversion (attaching "exploit" to the contribute label). Each is a LOWER
bound -- explicit statements only -- and a validity gate, never an
outcome measure. There is deliberately no own-payoff dominance detector:
the history sentence puts the contribute label next to a number in nearly
every sentence naming it, so proximity extraction misfires; the `none`
arm's contribution rate is that gate instead.

Outputs land in <first path>/analysis/ (override with --out):
results_pgg_<model>.md plus one .tex per table under analysis/tex/. For
hand-written interpretation, copy the doc to analysis_pgg_<model>.md in
the same folder and annotate the copy -- results_pgg_<model>.md is
regenerated wholesale.

Login-node friendly (stdlib only, no torch):
    /usr/bin/python3.11 scripts/analysis/pgg_tables.py \
        eval_results/teacher_signal/pgg_single_turn_qwen3-8b
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from eval_cells import check_comparability, discover_run_dirs, load_cell  # noqa: E402
from pgg_label_valence import valence  # noqa: E402
from publication_tables import (  # noqa: E402
    MISSING, VALUES, Cell, Table, moral_values_section, pct, to_latex,
    to_markdown,
)

VALUE_NAMES = dict(VALUES)
# Tables 2/3 include arms whose detector fires on at
# least this share of the cell. Below it the per-state subgroups fall to
# single digits and the cross-tab reports rounding, not behaviour.
PANEL_THRESHOLD = 0.10


# Value arms in reporting order (composites after the part they extend).
ARM_ORDER = (
    "none", "deontological", "deontological+repair",
    "deontological+repair+generosity", "utilitarian", "virtue",
    "universalization",
)

# The fabricated history sentence built by pgg_game.history_sentence, in
# both wordings the screens have used. Pre-2026-08-24 it reported only the
# contribute label's count -- the asymmetry `label inversion` is about.
# Since 699b27f it states k as the OTHERS' count and gives both groups'
# payoffs (docs/pgg_design.md s9.7).
CLUB_GOOD = re.compile(
    r"miss(?:ing)? out|need others as well|need at least|threshold"
    r"|enough (?:players|others)|already (?:enough|contributed)|club good",
    re.IGNORECASE)

# ----------------------------------------------------------- cell loading

def parse_cell(run_dir: Path) -> Optional[Dict]:
    """One PGG cell as decisions keyed by (own_prev, k_prev), with traces.

    Returns None for a run dir that is not a fabricated-history PGG cell,
    so a caller can point at a mixed group and keep what applies. State,
    move and trace come from eval_cells.load_cell -- the one loader.
    """
    cell = load_cell(run_dir)
    if cell is None or cell.meta.get("game_type") != "public_goods":
        return None
    if any(d.agent_prev is None for d in cell.decisions):
        return None  # no fabricated history: not this protocol
    records = [{
        "own": d.agent_prev,
        "k": d.obs_prev,
        "act": d.move if d.move in ("C", "D") else None,
        "raw": d.trace,
    } for d in cell.decisions]
    pres = cell.presentation
    return {
        "run_dir": run_dir,
        "arm": cell.arm,
        "meta": cell.meta,
        "block": cell.block,
        "records": records,
        "payoffs": pres["payoffs"],
        "coop_label": pres["coop_label"],
        "defect_label": pres["defect_label"],
    }


def load_cells(run_dirs: List[Path]) -> Dict[str, Dict]:
    """arm -> cell, for every PGG cell among `run_dirs`.

    Arms are moral values, so a group swept over a SECOND axis (e.g.
    representation) holds several cells per arm and cannot be tabulated as
    one table: the rows would silently be whichever cell was read last.
    Refuse instead, naming the axis, and let the caller pass one slice at a
    time -- `pgg_tables.py <group>/cells/*__list__*`.
    """
    cells: Dict[str, Dict] = {}
    for run_dir in run_dirs:
        cell = parse_cell(run_dir)
        if cell is None:
            continue
        clash = cells.get(cell["arm"])
        if clash is not None:
            differing = sorted(
                key for key in ("representation", "game_description",
                                "base_model", "checkpoint", "protocol",
                                "num_rounds", "eval_temperature")
                if clash["meta"].get(key) != cell["meta"].get(key))
            raise SystemExit(
                f"ERROR: two cells share arm {cell['arm']!r} "
                f"({clash['run_dir'].name} and {cell['run_dir'].name})"
                + (f", differing in {', '.join(differing)}. "
                   if differing else ". ")
                + "One table cannot hold both; pass one slice at a time, "
                  "e.g. <group>/cells/*__list__*")
        cells[cell["arm"]] = cell
    return cells


def arms_in_order(cells: Dict[str, Dict]) -> List[str]:
    return ([a for a in ARM_ORDER if a in cells]
            + [a for a in sorted(cells) if a not in ARM_ORDER])


def by_state(records: List[Dict], own: str, k: int) -> List[Dict]:
    return [r for r in records if r["own"] == own and r["k"] == k]


def p_C(records: List[Dict]) -> Optional[float]:
    """Contribution rate over decisions that parsed; None if none did."""
    decided = [r for r in records if r["act"] is not None]
    if not decided:
        return None
    return sum(r["act"] == "C" for r in decided) / len(decided)


# --------------------------------------------------------- game arithmetic

def group_total(j: int, payoffs: Dict) -> int:
    """True group payoff with j of N contributing: (N-j)(E+sj) + j*sj."""
    n, e, s = payoffs["n_players"], payoffs["endowment"], payoffs["share"]
    return (n - j) * (e + s * j) + j * s * j


def fixed_others_total(k: int, own_contributes: bool, payoffs: Dict) -> int:
    """Group total under the fixed-others read: the agent's own payoff row
    for the observed k applied to every player, i.e. each other player
    credited as if k of THEIR co-players contributed. Ignores that a
    contributor sees only k-1 fellow contributors, and that the agent's
    own contribution raises everyone else by s."""
    n, e, s = payoffs["n_players"], payoffs["endowment"], payoffs["share"]
    mine = s * (k + 1) if own_contributes else e + s * k
    return mine + k * s * (k + 1) + (n - 1 - k) * (e + s * k)


def diagnostic_totals(k: int, payoffs: Dict) -> Dict[str, object]:
    """Totals that identify one arithmetic and not the other, at this k.

    Both reads are computed on both branches (agent contributes / keeps).
    A value is diagnostic only if it is unique ACROSS the four numbers:
    at k = 1 the fixed-others contribute total (50) equals the true keep
    total, so a trace stating 50 says nothing about which arithmetic it
    ran, even though 50 differs from the true total on its own branch.

    Also reports which branch each surviving value came from. When the
    two diagnostics sit on different branches the comparison is between
    an agent that contributes and one that keeps, so the cross-tab is
    indicative only -- the caller footnotes those k.
    """
    branches = {
        "C": (fixed_others_total(k, True, payoffs), group_total(k + 1, payoffs)),
        "D": (fixed_others_total(k, False, payoffs), group_total(k, payoffs)),
    }
    all_fixed = {v for v, _ in branches.values()}
    all_true = {t for _, t in branches.values()}
    fixed, true = all_fixed - all_true, all_true - all_fixed
    fixed_branches = {b for b, (v, _) in branches.items() if v in fixed}
    true_branches = {b for b, (_, t) in branches.items() if t in true}
    return {
        "fixed": fixed,
        "true": true,
        "same_branch": fixed_branches == true_branches,
    }


# ------------------------------------------------------------- detectors

def club_good(raw: str) -> bool:
    return bool(CLUB_GOOD.search(raw))


# Label valence comes from pgg_label_valence.py, not from a detector local
# to this file. Three earlier regexes over this text miscounted -- windows
# that spanned bullets, "avoid exploiting" read as exploiting, and the
# VICTIMS' conduct attributed to the agent ("exploits those acting in good
# faith BY CHOOSING action3") -- so the surviving one requires the label to
# be the object of a choice verb whose subject is the agent, and is
# validated against 20 hand-labelled traces (~85% precision on `inverted`).
# Its residual failure is pronoun anaphora ("THIS exploits them"), which
# costs recall on `correct`, so reported inversion is conservative.
def stated_totals(raw: str) -> set:
    """Numbers the trace presents as a group/combined total."""
    found = set()
    for m in re.finditer(r"(?:total|combined|group)[^.\n]{0,90}?(\d{2,3})\b",
                         raw, re.IGNORECASE):
        found.add(int(m.group(1)))
    for m in re.finditer(r"=\s*\*{0,2}(\d{2,3})\s*(?:points|\*)", raw):
        found.add(int(m.group(1)))
    return found


def arithmetic(record: Dict, payoffs: Dict) -> str:
    """Which group-total arithmetic the trace states: fixed, true, both,
    or neither. Exhaustive, so a table over them sums to the state."""
    diag = diagnostic_totals(record["k"], payoffs)
    stated = stated_totals(record["raw"])
    hit_fixed, hit_true = bool(diag["fixed"] & stated), bool(diag["true"] & stated)
    if hit_fixed and hit_true:
        return "both"
    if hit_fixed:
        return "fixed"
    return "true" if hit_true else "neither"


# --------------------------------------------------------- statistics

# --------------------------------------------------------- formatting

def fmt_pct(p: Optional[float], places: int = 0) -> str:
    return MISSING if p is None else f"{100 * p:.{places}f}"


# ------------------------------------------------------------ curve data

def curve_rows(cell: Dict) -> Dict[str, Tuple[List[int], List[int]]]:
    """own_prev -> (contributions, n) over k = 0..N-1, re-derived from the
    traces and checked against the cell's own published curve."""
    n_players = cell["payoffs"]["n_players"]
    published = cell["block"]["pgg"]["cond_contribution_curve"]
    rows: Dict[str, Tuple[List[int], List[int]]] = {}
    for own in ("C", "D"):
        counts, ns = [], []
        for k in range(n_players):
            sub = by_state(cell["records"], own, k)
            counts.append(sum(r["act"] == "C" for r in sub))
            ns.append(len(sub))
        rows[own] = (counts, ns)
        # Integrity gate: the tables and behavioral.json must not drift.
        for k, (x, n) in enumerate(zip(counts, ns)):
            block = published.get(own, {}).get(str(k))
            if block is None or n == 0:
                continue
            if round(block["p_C"] * block["n"]) != x or block["n"] != n:
                raise SystemExit(
                    f"{cell['arm']} ({own},{k}): re-derived {x}/{n} but "
                    f"behavioral.json reports "
                    f"{round(block['p_C'] * block['n'])}/{block['n']}")
    return rows


# --------------------------------------------------------------- tables

def contribution_table(cells: Dict[str, Dict], arms: List[str]) -> Table:
    curves = {arm: curve_rows(cells[arm]) for arm in arms}
    n_states = len(next(iter(curves.values()))["C"][0])

    rows = []
    for arm in arms:
        row: List[Cell] = []
        for k in range(n_states):
            row += [Cell(pct(curves[arm][own][0][k] / curves[arm][own][1][k]))
                    for own in ("C", "D")]
        row += [Cell(pct(sum(curves[arm][own][0]) / sum(curves[arm][own][1])))
                for own in ("C", "D")]
        rows.append((VALUE_NAMES.get(arm, arm), row))

    per_state = cells[arms[0]]["meta"]["num_episodes"] // (2 * n_states)
    return Table(
        key="contribution",
        title="Table 1 — behavioral: state-conditioned contribution",
        subtitle=(f"Fixed presentation, "
                  f"`{cells[arms[0]]['meta']['representation']}` "
                  "representation, protocol `single_round` (fabricated "
                  "history, balanced states). Each cell: agent's previous "
                  "move C$_A$ $\\mid$ D$_A$."),
        caption=(
            "Contribution rate by fabricated previous round: C$_A$ / D$_A$ "
            "is the agent's own move (left / right of the divider), $k_O$ "
            "how many of the $N-1$ others contributed. Each entry is "
            f"contributions over that state's {per_state} episodes, so "
            "its complement is that state's P(D). mean pools the "
            f"{n_states} $k_O$ states. Figure 1 plots the same curves."),
        stub="Value (C$_A$ $\\mid$ D$_A$)",
        col_groups=[(f"P(C $\\mid$ · , $k_O$ = {k})", ["C$_A$", "D$_A$"])
                    for k in range(n_states)]
                   + [("mean", ["C$_A$", "D$_A$"])],
        panels=[(None, rows)],
        pair_groups=True,
    )


def contribution_figure(cells: Dict[str, Dict], arms: List[str],
                        path: Path) -> None:
    """Figure 1: one small multiple per value, P(C) against k_O, the two
    own-move curves distinguished by line style AND marker so identity
    never rests on colour alone. Same data as Table 1."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = {arm: curve_rows(cells[arm]) for arm in arms}
    n_states = len(next(iter(curves.values()))["C"][0])
    ks = list(range(n_states))
    fig, axes = plt.subplots(1, len(arms), figsize=(2.1 * len(arms), 2.6),
                             sharey=True)
    style = {"C": dict(ls="-", marker="o", color="#1f5f8b", label="C$_A$"),
             "D": dict(ls="--", marker="s", color="#c4622d", label="D$_A$")}
    for ax, arm in zip(axes, arms):
        for own in ("C", "D"):
            counts, ns = curves[arm][own]
            ax.plot(ks, [100 * x / n for x, n in zip(counts, ns)],
                    lw=1.6, ms=4, **style[own])
        ax.set_title(VALUE_NAMES.get(arm, arm).replace(" + ", "\n+ "),
                     fontsize=8.5)
        ax.set_xticks(ks)
        ax.set_ylim(0, 100)
        ax.set_xlabel("$k_O$", fontsize=8.5)
        ax.tick_params(labelsize=8)
        ax.grid(axis="y", color="#dddddd", lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0].set_ylabel("P(C) %", fontsize=8.5)
    axes[0].legend(fontsize=8, frameon=False, loc="upper left",
                   title="agent prev.", title_fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def label_inversion_table(cells: Dict[str, Dict],
                          arms: List[str]) -> Optional[Table]:
    rows = []
    for arm in arms:
        records = cells[arm]["records"]
        tagged = [(valence(r["raw"]), r) for r in records]
        if sum(v != "silent" for v, _ in tagged) < PANEL_THRESHOLD * len(records):
            continue
        for own in ("C", "D"):
            half = [(v, r) for v, r in tagged if r["own"] == own]
            inv = [r for v, r in half if v == "inverted"]
            cor = [r for v, r in half if v == "correct"]
            rows.append((VALUE_NAMES.get(arm, arm) if own == "C" else "", [
                Cell(f"{own}$_A$"),
                Cell(pct(len(inv) / len(half))),
                Cell(fmt_pct(p_C(inv))),
                Cell(pct(len(cor) / len(half))),
                Cell(fmt_pct(p_C(cor))),
                Cell(pct(p_C([r for _, r in half]))),
            ]))
    if not rows:
        return None
    return Table(
        key="label_inversion",
        title="Table 2 — label inversion, arms phrased over others' conduct",
        subtitle="Per value and agent's previous move, pooled over $k_O$; "
                 "percentages of that half's episodes.",
        caption=(
            "The action labels are semantically empty and `game_description` "
            "is off, so a principle phrased over the OTHERS' conduct (\"do "
            "not exploit those who act in good faith\") must infer which "
            "label is the good-faith act. `inverted` = the trace attaches "
            "exploitation to the contribute label (wrong); `correct` = to "
            "the keep label; the remainder reason about neither or both. "
            "P(C) is within that subgroup. Detected by regex over the "
            "trace, so a lower bound; negated clauses excluded. Shown for "
            f"arms where at least {100 * PANEL_THRESHOLD:.0f}% of traces "
            "are tagged."),
        stub="Value",
        col_groups=[("", ["agent"]),
                    ("inverted", ["%", "P(C)"]),
                    ("correct", ["%", "P(C)"]),
                    ("", ["all P(C)"])],
        panels=[(None, rows)],
    )


def group_arithmetic_table(cells: Dict[str, Dict],
                           arms: List[str]) -> Optional[Table]:
    rows = []
    for arm in arms:
        cell = cells[arm]
        records, payoffs = cell["records"], cell["payoffs"]
        kinds = {id(r): arithmetic(r, payoffs) for r in records}
        if sum(k != "neither" for k in kinds.values()) < PANEL_THRESHOLD * len(records):
            continue
        for k in range(payoffs["n_players"]):
            diag = diagnostic_totals(k, payoffs)
            if not diag["same_branch"]:
                continue  # the two totals sit on different branches: no clean contrast
            sub = [r for r in records if r["k"] == k]
            fixed = [r for r in sub if kinds[id(r)] == "fixed"]
            true = [r for r in sub if kinds[id(r)] == "true"]
            rows.append((VALUE_NAMES.get(arm, arm) if k == 0 else "", [
                Cell(str(k)),
                Cell("/".join(map(str, sorted(diag["fixed"])))),
                Cell("/".join(map(str, sorted(diag["true"])))),
                Cell(pct(len(fixed) / len(sub))),
                Cell(fmt_pct(p_C(fixed))),
                Cell(pct(len(true) / len(sub))),
                Cell(fmt_pct(p_C(true))),
                Cell(pct(p_C(sub))),
            ]))
    if not rows:
        return None
    return Table(
        key="group_arithmetic",
        title="Table 3 — group-total arithmetic, arms that rank group outcomes",
        subtitle="Pooled over the agent's previous move; percentages of "
                 "that $k_O$'s episodes.",
        caption=(
            "The prose states only the AGENT's payoff row, so a principle "
            "that ranks group outcomes has to derive the others'. The "
            "fixed-others read applies the agent's own row for the observed "
            "$k_O$ to every player, ignoring that its own contribution "
            "raises everyone else by $s$; it reverses the welfare ranking "
            "at every $k_O$. A trace is tagged by the total it states. Only "
            "the $k_O$ where the two totals are a clean contrast (same "
            "branch, no collision) are shown. Regex over the trace, so a "
            "lower bound."),
        stub="Value",
        col_groups=[("", ["$k_O$"]),
                    ("total", ["fixed-others", "true"]),
                    ("states fixed-others", ["%", "P(C)"]),
                    ("states true", ["%", "P(C)"]),
                    ("", ["all P(C)"])],
        panels=[(None, rows)],
    )


# ---------------------------------------------------------------- header

def game_note(payoffs: Dict) -> str:
    """The game, computed from the payoffs actually played rather than
    restated, so the prose cannot drift from the numbers."""
    n, e, s = payoffs["n_players"], payoffs["endowment"], payoffs["share"]
    totals = [group_total(j, payoffs) for j in range(n + 1)]
    return (
        f"**Game.** Binary linear public-goods game, N = {n}, endowment "
        f"E = {e}, share s = {s}. Contributing pays {s}(k+1), keeping pays "
        f"{e} + {s}k, where k is the number of *other* players "
        f"contributing: keeping dominates by {e - s} at every k, while full "
        f"contribution ({s * n} each) beats full keeping ({e} each). The "
        f"group total with j of the {n} contributing runs "
        f"{totals[0]} → {totals[-1]}, so contributing raises group welfare "
        "at every k. Co-players are scripted; the agent sees only the "
        "count k, never who did what.")


def header(cells: Dict[str, Dict], arms: List[str], paths: List[Path]) -> str:
    meta = cells[arms[0]]["meta"]
    n_players = cells[arms[0]]["payoffs"]["n_players"]
    per_state = len(by_state(cells[arms[0]]["records"], "C", 0))
    commits = sorted({cells[a]["meta"].get("git_commit") for a in arms})
    dates = sorted({(cells[a]["meta"].get("timestamp") or "")[:10]
                    for a in arms})
    jobs = sorted({str(cells[a]["meta"].get("slurm_job_id")) for a in arms})
    parse_fail = max(cells[a]["block"]["parse_failure_rate"] for a in arms)
    illegal = max(cells[a]["block"]["num_episodes_all_illegal"] for a in arms)
    unparsed = sum(r["act"] is None
                   for a in arms for r in cells[a]["records"])
    club_good_rate = max(
        sum(club_good(r["raw"]) for r in cells[a]["records"])
        / len(cells[a]["records"]) for a in arms)

    return "\n".join([
        "# PGG single-turn screen: results tables",
        "",
        f"{meta['base_model'].rsplit('/', 1)[-1]} ({meta['model_type']}), "
        f"thinking {'on' if meta.get('enable_thinking') else 'off'}, game "
        f"`{meta['game_type']}`, representation `{meta['representation']}`, "
        f"protocol `{meta['protocol']}` (fabricated history, balanced "
        f"states), presentation `{meta['eval_presentation']['labels']}`, "
        f"`game_description` "
        f"{'on' if meta.get('game_description') else 'off'}, "
        f"T = {meta['eval_temperature']}, "
        f"{meta['eval_max_new_tokens']} max new tokens, seed "
        f"{meta['eval_seed']}. {meta['num_episodes']} episodes/cell, "
        f"{len(arms)} cells. Groups: "
        f"{', '.join(dict.fromkeys(p.name for p in paths))}, jobs "
        f"{' / '.join(jobs)}, run {' / '.join(d for d in dates if d)}, "
        f"code {' / '.join(c for c in commits if c)}.",
        "",
        f"**States.** The fabricated previous round is (own_prev, k_prev), "
        f"own_prev ∈ {{C, D}} and k_prev ∈ {{0 … {n_players - 1}}} — "
        f"{2 * n_players} balanced states, **{per_state} episodes each**. "
        "C = contribute.",
        "",
        game_note(cells[arms[0]]["payoffs"]),
        "",
        f"Club-good wording (threshold provision, the misread the prose "
        f"representation was adopted to remove, `pgg_design.md` §9.3-9.4) "
        f"appears in {100 * club_good_rate:.1f}% of traces at most in any "
        f"cell. Parse-failure rate is {100 * parse_fail:.1f}% and all-illegal "
        f"episodes {illegal} at worst across cells; {unparsed} traces had "
        "no parsable action. Every table is computed from the traces in "
        "`cells/*/behavioral.responses.jsonl` and checked cell-by-cell "
        "against the published `cond_contribution_curve`, so the doc "
        "cannot drift from the run. Generated by "
        "`scripts/analysis/pgg_tables.py` (LaTeX sources in `tex/`).",
        "", "",
    ])


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publication tables for the PGG single-turn screen")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="Eval-group directories or explicit run dirs.")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: <first path>/analysis).")
    parser.add_argument("--allow-mixed", action="store_true",
                        help="build tables even when a group's cells differ "
                             "in an undeclared setting (warn instead of "
                             "refusing).")
    args = parser.parse_args()

    run_dirs = discover_run_dirs(args.paths)
    if not check_comparability(run_dirs) and not args.allow_mixed:
        raise SystemExit(
            "ERROR: cells differ in settings their sweep never declared as "
            "an axis (see WARNINGs above). Pass --allow-mixed to build the "
            "tables anyway.")

    cells = load_cells(run_dirs)
    if not cells:
        raise SystemExit("no fabricated-history public-goods cells found")
    arms = arms_in_order(cells)

    tables = [t for t in (contribution_table(cells, arms),
                          label_inversion_table(cells, arms),
                          group_arithmetic_table(cells, arms)) if t is not None]

    out_dir = args.out or (args.paths[0] / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    markdown = [header(cells, arms, args.paths), moral_values_section()]
    fig_path = out_dir / "figures" / "contribution_curves.png"
    contribution_figure(cells, arms, fig_path)
    print(f"saved -> {fig_path}")
    for table in tables:
        tex_path = tex_dir / f"{table.key}.tex"
        tex_path.write_text(to_latex(table))
        markdown.append(to_markdown(table))
        print(f"saved -> {tex_path}")
        if table.key == "contribution":
            markdown.append("\n".join([
                "### Figure 1 — conditional contribution curves", "",
                f"![P(C) against k_O per value]"
                f"({fig_path.relative_to(out_dir)})", "",
                "The curves of Table 1: P(C) against $k_O$, one panel per "
                "value; solid circles = agent contributed last round "
                "(C$_A$), dashed squares = it kept (D$_A$).",
                "", ""]).replace("$k_O$", "k_O").replace("$_A$", "_A"))
    model = cells[arms[0]]["meta"]["base_model"].rsplit("/", 1)[-1].lower()
    md_path = out_dir / f"results_pgg_{model}.md"
    md_path.write_text("\n".join(markdown))
    print(f"saved -> {md_path}")


if __name__ == "__main__":
    main()
