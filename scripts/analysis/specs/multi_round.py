#!/usr/bin/env python3.11
"""Results document for a multi-round (in-play) checkpoint eval.

One spec for BOTH game families and both results roots -- the multi-round
study deliberately spans them (post_training/<model>/classic/multi_round =
the training game in play; transfer/<model>/pgg/multi_round = the commons
in play), and this spec is what unifies it on the report side. Episodes
are live conversations: cold open (no fabricated history), rules in round
1 only, one outcome message per later round; states are the REALIZED
(own last move, observation) pairs, not fabricated ones.

Tables:
    M1  P(C) per round, one panel per opponent -- the dynamics headline.
    M2  P(C | state) measured in play (rounds >= 2, pooled over
        opponents), next to the same policy's single-round
        fabricated-history row (--reference) -- the surface-consistency
        check.
    M3  episode outcomes per policy x opponent: opening P(C), final-round
        P(C), share of episodes ending in mutual cooperation.
    M4  trace measures (normative vocabulary, verbatim recitation).

Figure: P(C) against round, one panel per opponent, one line per policy.

    /usr/bin/python3.11 scripts/analysis/make_results.py \\
        eval_results/post_training/qwen3_8b/classic/multi_round \\
        --reference eval_results/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder \\
        --reference eval_results/post_training/qwen3_8b_sdpo_pd_deon-repair-gen_tft_200/classic/ckpt_ladder
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from eval_cells import check_comparability, discover_run_dirs, load_json  # noqa: E402
from measures import normative_hit, principle_ngrams, principle_overlap  # noqa: E402
from results_doc import MISSING, Cell, Table, pct, to_latex, to_markdown  # noqa: E402
from specs.post_training import CKPT_RE, checkpoint_of  # noqa: E402
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402

# Pre-migration cells recorded the legacy run-dir names in metadata
# (docs/naming.md legacy table); normalize so reference pairing works.
LEGACY_RUNS = {
    "grpo_deon_tft_200": "qwen3_8b_grpo_pd_deon_tft_200",
    "grpo_util_tft_150": "qwen3_8b_grpo_pd_util_tft_150",
    "qwen_run2_200": "qwen3_8b_sdpo_pd_deon-repair-gen_tft_200",
    "gemma_run2_200": "gemma2_9b_sdpo_pd_deon-repair-gen_tft_200",
    "llama31_deon_150_v2": "llama31_8b_grpo_pd_deon_tft_150",
}


def ckpt_key(meta) -> str:
    run, step = checkpoint_of(meta)
    return f"{LEGACY_RUNS.get(run, run)}|{step}"


# ------------------------------------------------------------- loading

class MultiCell:
    """One multi-round cell: metadata, one result block per opponent,
    and the raw per-round decisions."""
    __slots__ = ("run_dir", "meta", "blocks", "decisions")

    def __init__(self, run_dir, meta, blocks, decisions):
        self.run_dir, self.meta = run_dir, meta
        self.blocks, self.decisions = blocks, decisions


def load_multi_cell(run_dir: Path) -> Optional[MultiCell]:
    beh = load_json(run_dir, "behavioral.json")
    resp = run_dir / "behavioral.responses.jsonl"
    if beh is None or not resp.exists():
        return None
    meta = beh["metadata"]
    if meta.get("protocol") != "multi_round":
        return None
    blocks = {o["opponent"]: o for o in beh["opponents"]}
    with resp.open() as f:
        decisions = [json.loads(line) for line in f]
    return MultiCell(run_dir, meta, blocks, decisions)


def policy_label(meta: Dict) -> str:
    run, step = checkpoint_of(meta)
    if run == "base":
        return "base"
    fields = run.split("_")
    channel = f"{fields[2].upper()} {fields[4]}" if len(fields) >= 6 else run
    return f"{channel} {'final' if str(step) == 'final' else f's{step}'}"


def collect(group: Path) -> List[Tuple[str, MultiCell]]:
    cells = []
    for run_dir in discover_run_dirs([group]):
        cell = load_multi_cell(run_dir)
        if cell is not None:
            cells.append((policy_label(cell.meta), cell))
    order = {lbl: (checkpoint_of(c.meta) != ("base", 0), checkpoint_of(c.meta))
             for lbl, c in cells}
    return sorted(cells, key=lambda lc: order[lc[0]])


def reference_rows(groups: List[Path], game: str) -> Dict[str, Dict]:
    """checkpoint string -> single-round state_conditioning block, from
    fabricated-history cells of the same game (none arm)."""
    found: Dict[str, Dict] = {}
    for run_dir in discover_run_dirs(groups):
        beh = load_json(run_dir, "behavioral.json")
        if beh is None:
            continue
        meta = beh["metadata"]
        if (meta.get("game_type") != game or meta.get("moral_value") != "none"
                or meta.get("protocol") == "multi_round"):
            continue
        block = beh["opponents"][0] if isinstance(beh["opponents"], list) \
            else beh["opponents"]["random"]
        found.setdefault(ckpt_key(meta), block.get("state_conditioning") or {})
    return found


# -------------------------------------------------------------- tables

def state_cols(cells: List[Tuple[str, MultiCell]]) -> List[str]:
    keys = sorted({s for _, c in cells for b in c.blocks.values()
                   for s in (b.get("state_conditioning") or {})})
    return keys


def fmt_state(s: str) -> str:
    own, obs = s.strip("()").split(",")
    return (f"{own}$_A${obs}$_O$" if obs in ("C", "D")
            else f"{own}$_A$,k={obs}")


def per_round_table(cells, opponents: List[str], n_rounds: int) -> Table:
    panels = []
    for opp in opponents:
        rows = []
        for label, cell in cells:
            block = cell.blocks[opp]
            pr = block.get("per_round") or {}
            row = [Cell(pct(pr[f"round_{r}"]["p_C"]) if f"round_{r}" in pr
                        else MISSING) for r in range(1, n_rounds + 1)]
            row.append(Cell(pct(block["cooperation_rate"]), bold=True))
            rows.append((label, row))
        panels.append((f"vs `{opp}`", rows))
    return Table(
        key="mr-per-round",
        title="Table M1 — cooperation per round, in play",
        subtitle=("Live episodes: cold open (round 1 has no history), one "
                  "conversation per episode, moves simultaneous."),
        caption=("P(C) per round over that opponent's episodes (20 per "
                 "cell, binomial s.e. $\\leq$11 points/round). pooled = "
                 "all decisions. Round 1 is the untrained opening state; "
                 "later rounds condition on the episode's own history."),
        stub="Policy",
        col_groups=[(None, [f"r{r}"]) for r in range(1, n_rounds + 1)]
        + [(None, ["pooled"])],
        panels=panels,
    )


def live_state_table(cells, refs: Dict[str, Dict]) -> Table:
    states = state_cols(cells)
    rows = []
    for label, cell in cells:
        agg: Dict[str, List[float]] = {}
        for b in cell.blocks.values():
            for s, v in (b.get("state_conditioning") or {}).items():
                agg.setdefault(s, [0, 0])
                agg[s][0] += v["p_C"] * v["n"]
                agg[s][1] += v["n"]
        row = []
        for s in states:
            c, n = agg.get(s, (0, 0))
            row.append(Cell(f"{pct(c / n)}" if n else MISSING,
                            marker="" if n >= 10 else "*"))
        rows.append((label, row))
        ref = refs.get(ckpt_key(cell.meta))
        if ref:
            row2 = []
            for s in states:
                v = ref.get(s) or ref.get(f"({s.strip('()')})")
                row2.append(Cell(pct(v["p_C"]) if v else MISSING))
            rows.append((f"  · single-round (fabricated)", row2))
    return Table(
        key="mr-live-states",
        title="Table M2 — the state table measured in play",
        subtitle=("Rounds $\\geq$2 pooled over opponents and rounds; states "
                  "are the episode's own realized (last move, observation) "
                  "pairs, so their frequencies are policy-dependent and "
                  "UNBALANCED."),
        caption=("P(C $\\mid$ state) in live play, under each policy its "
                 "single-round fabricated-history row where evaluated "
                 "(--reference): matching numbers mean the installed table "
                 "survives the conversation surface. * marks live cells "
                 "with n $<$ 10 -- read those as anecdotes."),
        stub="Policy",
        col_groups=[(None, [fmt_state(s)]) for s in states],
        panels=[(None, rows)],
        notes=["Live n per cell varies by policy (a policy that never "
               "reaches a state contributes no estimate there)."],
    )


def outcomes_table(cells, opponents: List[str], n_rounds: int,
                   coop_obs) -> Table:
    rows = []
    for label, cell in cells:
        for i, opp in enumerate(opponents):
            eps: Dict[int, List[Dict]] = {}
            for d in cell.decisions:
                if d["opponent"] == opp:
                    eps.setdefault(d["episode"], []).append(d)
            n = len(eps)
            opening = sum(any(x["round"] == 1 and x["agent_move"] == "C"
                              for x in e) for e in eps.values()) / n
            last = [max(e, key=lambda x: x["round"]) for e in eps.values()]
            final_c = sum(x["agent_move"] == "C" for x in last) / n
            mutual = sum(x["agent_move"] == "C" and coop_obs(x["obs"])
                         for x in last) / n
            rows.append((label if i == 0 else "", [
                Cell(f"`{opp}`"), Cell(pct(opening)), Cell(pct(final_c)),
                Cell(pct(mutual)), Cell(str(n))]))
    return Table(
        key="mr-outcomes",
        title="Table M3 — episode outcomes",
        subtitle="Per policy and opponent, over that cell's episodes.",
        caption=("open = P(C) in round 1, the state training never showed. "
                 f"final = P(C) in round {n_rounds}. mutual coop = share of "
                 "episodes whose LAST round is jointly cooperative (agent C "
                 "and the opponent/majority of co-players C) -- the "
                 "absorption readout."),
        stub="Policy",
        col_groups=[(None, ["opponent"]), (None, ["open"]),
                    (None, ["final"]), (None, ["mutual coop"]), (None, ["episodes"])],
        panels=[(None, rows)],
    )


def trace_table(cells, principle: str) -> Table:
    grams = principle_ngrams(get_moral_value(principle))
    rows = []
    for label, cell in cells:
        raws = [d["raw"] for d in cell.decisions]
        n = len(raws)
        rows.append((label, [
            Cell(str(n)),
            Cell(pct(sum(normative_hit(t) for t in raws) / n)),
            Cell(pct(sum(principle_overlap(t, grams) for t in raws) / n))]))
    return Table(
        key="mr-traces",
        title="Table M4 — reasoning traces in play",
        subtitle="All rounds and opponents pooled.",
        caption=(f"normative \\% = trace contains a reviewed moral word "
                 f"stem; recites \\% = reproduces $\\geq$6 consecutive "
                 f"words of the `{principle}` wording (verbatim only). "
                 "The principle text is in none of these prompts."),
        stub="Policy",
        col_groups=[(None, ["n"]), (None, ["normative \\%"]),
                    (None, ["recites \\%"])],
        panels=[(None, rows)],
    )


# -------------------------------------------------------------- figure

def per_round_figure(cells, opponents: List[str], n_rounds: int,
                     out_dir: Path, name: str,
                     model: str = "") -> Path:
    from figure_style import (REF_BASE, RUN_COLORS, WIDTHS, apply_style,
                              clean_axes, direct_labels, save)
    import matplotlib.pyplot as plt
    apply_style()
    fig, axes = plt.subplots(1, len(opponents), sharey=True, squeeze=False,
                             figsize=(WIDTHS["wide"], 2.2),
                             gridspec_kw={"wspace": 0.12})
    trained = [lc for lc in cells if lc[0] != "base"]
    for j, opp in enumerate(opponents):
        ax = axes[0][j]
        labels = []
        rounds = list(range(1, n_rounds + 1))
        for label, cell in cells:
            label = label.split()[0] + (" " + label.split()[-1]
                                        if label != "base" else "")
            pr = cell.blocks[opp].get("per_round") or {}
            ys = [100 * pr[f"round_{r}"]["p_C"] for r in rounds
                  if f"round_{r}" in pr]
            if label == "base":
                ax.plot(rounds, ys, **REF_BASE)
                labels.append((ys[-1], "base", REF_BASE["color"]))
            else:
                shorts = [l.split()[0] + " " + l.split()[-1]
                          for l, _ in trained]
                i = shorts.index(label)
                color = RUN_COLORS[i % len(RUN_COLORS)]
                ax.plot(rounds, ys, color=color, marker="o", ms=3, lw=1.4)
                labels.append((ys[-1], label, color))
        direct_labels(ax, labels, rounds[-1], min_gap=8.0)
        ax.set_xlim(0.8, n_rounds + 1.6)
        ax.set_xticks(rounds)
        ax.set_ylim(-3, 103)
        ax.set_title(f"vs {opp}", fontsize=8)
        ax.set_xlabel("round", fontsize=7.5)
        if j == 0:
            ax.set_ylabel("P(C) (%)", fontsize=7.5)
        clean_axes(ax)
    if model:
        fig.suptitle(model, y=1.04, fontsize=9)
    fig.subplots_adjust(left=0.07, right=0.99, top=0.86, bottom=0.22)
    return save(fig, out_dir / "figures", f"multi_round_{name}")[0]



JOINT_TEX = {"CC": "CC (mutual coop)", "CD": "C vs D (suckered)",
             "DC": "D vs C (exploiting)", "DD": "DD (mutual defect)",
             "Chi": "C, k>=2", "Clo": "C, k<2",
             "Dhi": "D, k>=2 (free-riding)", "Dlo": "D, k<2", "ill": "illegal"}


def _episodes(cell: MultiCell, opp: str) -> Dict[int, Dict[int, Dict]]:
    eps: Dict[int, Dict[int, Dict]] = {}
    for d in cell.decisions:
        if d["opponent"] == opp:
            eps.setdefault(d["episode"], {})[d["round"]] = d
    return eps


def _joint(d: Dict, pgg: bool) -> str:
    a = d["agent_move"]
    if a not in ("C", "D"):
        return "ill"
    if not pgg:
        return a + d["obs"]
    return a + ("hi" if isinstance(d["obs"], int) and d["obs"] >= 2 else "lo")


def stacked_figure(cells, opp: str, n_rounds: int, pgg: bool,
                   out_dir: Path, name: str) -> Path:
    """Joint-outcome composition per round vs the REACTIVE opponent: the
    view that explains the per-round line (absorption = one band taking
    over; the repair-retaliate cycle = DC and CD swapping per round)."""
    from figure_style import STATE_COLORS, WIDTHS, apply_style, clean_axes, save
    import matplotlib.pyplot as plt
    import numpy as np
    apply_style()
    cats = (["Chi", "Clo", "Dhi", "Dlo", "ill"] if pgg
            else ["CC", "CD", "DC", "DD", "ill"])
    colors = dict(zip(cats[:4], [STATE_COLORS[s] for s in
                                 ("CC", "CD", "DC", "DD")]), ill="#bbbbbb")
    fig, axes = plt.subplots(1, len(cells), sharey=True, squeeze=False,
                             figsize=(WIDTHS["wide"], 2.3),
                             gridspec_kw={"wspace": 0.08})
    rounds = list(range(1, n_rounds + 1))
    for j, (label, cell) in enumerate(cells):
        ax = axes[0][j]
        eps = _episodes(cell, opp)
        bottom = np.zeros(len(rounds))
        for c in cats:
            ys = [100 * sum(_joint(e[r], pgg) == c for e in eps.values())
                  / len(eps) for r in rounds]
            ax.bar(rounds, ys, bottom=bottom, color=colors[c], width=0.82,
                   edgecolor="white", linewidth=0.4)
            bottom += np.array(ys)
        ax.set_title(label, fontsize=8)
        ax.set_xticks(rounds)
        ax.set_ylim(0, 100)
        ax.set_xlabel("round", fontsize=7.5)
        clean_axes(ax)
        ax.grid(False)
        if j == 0:
            ax.set_ylabel("share of episodes (%)", fontsize=7.5)
    import matplotlib.patches as mp
    fig.legend([mp.Rectangle((0, 0), 1, 1, color=colors[c]) for c in cats],
               [JOINT_TEX[c] for c in cats], loc="upper center",
               ncols=len(cats), frameon=False, bbox_to_anchor=(0.5, 1.02),
               fontsize=7)
    fig.suptitle(f"joint outcome per round — vs {opp} only", y=1.12,
                 fontsize=9)
    fig.subplots_adjust(left=0.07, right=0.99, top=0.78, bottom=0.22)
    return save(fig, out_dir / "figures", f"multi_round_stacked_{name}")[0]


OPP_TOKEN = {"tit_for_tat": "Tft", "always_defect": "Alld",
             "always_cooperate": "Allc", "noisy_tft": "Ntft",
             "full_contributor": "Full", "free_rider": "Free",
             "noisy_conditional": "Noisy"}


def write_numbers(cells, opponents, n_rounds, coop_obs, refs,
                  out_dir: Path, game: str, prefix: str | None = None) -> Path:
    """LaTeX number macros for the report prose (letters-only names):
    \<prefix><Policy><Opp><Open|Final|Pooled|Mutual> in percent, plus
    per-policy live/fabricated state strings. Same data as the tables,
    so prose and figures cannot disagree."""
    prefix = prefix or ("MRPD" if game != "public_goods" else "MRPGG")
    if not prefix.isalpha():
        raise SystemExit(f"--macro-prefix must be letters only, got {prefix!r}")
    # policy tokens: channel word; ordinal suffix when a channel repeats
    by_channel: Dict[str, List[str]] = {}
    for label, _ in cells:
        ch = "Base" if label == "base" else label.split()[0].capitalize()
        by_channel.setdefault(ch, []).append(label)
    tok = {}
    for ch, labels in by_channel.items():
        if len(labels) == 1:
            tok[labels[0]] = ch
        else:
            names = ["Early", "Mid", "Late", "Final"][:len(labels)]
            names[-1] = "Late"
            for lbl, suffix in zip(labels, names):
                tok[lbl] = ch + suffix
    lines = ["% Generated by scripts/analysis/specs/multi_round.py -- do not edit."]

    def emit(name, value):
        lines.append(f"\\newcommand{{\\{prefix}{name}}}{{{value}}}")

    grpo_pooled = []
    for label, cell in cells:
        for opp in opponents:
            block = cell.blocks[opp]
            eps: Dict[int, List[Dict]] = {}
            for d in cell.decisions:
                if d["opponent"] == opp:
                    eps.setdefault(d["episode"], []).append(d)
            n = len(eps)
            last = [max(e, key=lambda x: x["round"]) for e in eps.values()]
            pr = block.get("per_round") or {}
            vals = {
                "Open": round(100 * pr["round_1"]["p_C"]),
                "Final": round(100 * pr[f"round_{n_rounds}"]["p_C"]),
                "Pooled": round(100 * block["cooperation_rate"]),
                "Mutual": round(100 * sum(x["agent_move"] == "C"
                                          and coop_obs(x["obs"])
                                          for x in last) / n),
            }
            for metric, v in vals.items():
                emit(f"{tok[label]}{OPP_TOKEN[opp]}{metric}", v)
            if tok[label] == "Grpo":
                grpo_pooled.append(vals["Pooled"])
        # live / fabricated state strings (2x2 order; PGG strings too long)
        if game != "public_goods":
            agg: Dict[str, List[float]] = {}
            for b in cell.blocks.values():
                for s, v in (b.get("state_conditioning") or {}).items():
                    agg.setdefault(s, [0, 0])
                    agg[s][0] += v["p_C"] * v["n"]
                    agg[s][1] += v["n"]
            order = ["(C,C)", "(C,D)", "(D,C)", "(D,D)"]
            live = "/".join(str(round(100 * agg[s][0] / agg[s][1]))
                            if agg.get(s, [0, 0])[1] else "--" for s in order)
            emit(f"{tok[label]}LiveStates", live)
            ref = refs.get(ckpt_key(cell.meta))
            if ref:
                emit(f"{tok[label]}FabStates", "/".join(
                    str(round(100 * ref[s]["p_C"])) if s in ref else "--"
                    for s in order))
    if grpo_pooled:
        emit("GrpoPooledMin", min(grpo_pooled))
        emit("GrpoPooledMax", max(grpo_pooled))
    gen = out_dir / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    path = gen / f"numbers_{prefix}.tex"
    path.write_text("\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------- header

def header(cells, group: Path, opponents: List[str], n_rounds: int) -> str:
    meta = cells[0][1].meta
    model = meta["base_model"].rsplit("/", 1)[-1]
    jobs = sorted({str(c.meta.get("slurm_job_id")) for _, c in cells})
    pf = max(b["parse_failure_rate"] for _, c in cells
             for b in c.blocks.values())
    return "\n".join([
        f"# Multi-round eval: {group.parent.parent.name} / "
        f"{group.parent.name} / {group.name}", "",
        f"{model}, game `{meta['game_type']}`, representation "
        f"`{meta['representation']}`, {n_rounds} live rounds per episode, "
        f"cold open (no fabricated history; rules in round 1 only, one "
        f"outcome message per later round, conversation accumulates), "
        f"opponents {', '.join(f'`{o}`' for o in opponents)}, "
        f"20 episodes per opponent per policy, T = "
        f"{meta['eval_temperature']}, no moral text in any prompt. Jobs "
        f"{' / '.join(jobs)}; parse-failure at worst {100 * pf:.1f}%.", "",
        "Policies are the single-step-trained checkpoints; this document "
        "measures them IN PLAY. The single-round tables are the same "
        "policies' fabricated-history rows (Table M2 pairs them).",
        "", ""])


# ----------------------------------------------------------------- main

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", action="append", type=Path, default=[],
                        help="single-round eval group(s) holding the same "
                             "policies' fabricated-history cells (repeatable)")
    parser.add_argument("--principle", default="deontological+repair+generosity")
    parser.add_argument("--macro-prefix", default=None,
                        help="LaTeX macro prefix for generated/numbers_<prefix>.tex "
                             "(letters only; default MRPD / MRPGG by game). Give a "
                             "distinct prefix when several groups of one game are "
                             "mirrored into the same report.")


def build(args: argparse.Namespace) -> Path:
    """Multi-round in-play eval: per-round dynamics, live state table vs
    fabricated-history rows, episode outcomes, trace measures."""
    group = args.paths[0]
    if not check_comparability(discover_run_dirs([group])) and not args.allow_mixed:
        raise SystemExit("ERROR: cells differ in an undeclared setting "
                         "(see WARNINGs). --allow-mixed to proceed.")
    cells = collect(group)
    if not cells:
        raise SystemExit(f"{group}: no multi-round cells")
    meta = cells[0][1].meta
    opponents = list(cells[0][1].blocks)
    n_rounds = max(int(k.split("_")[1]) for _, c in cells
                   for b in c.blocks.values() for k in (b.get("per_round") or {}))
    game = meta["game_type"]
    coop_obs = ((lambda obs: obs == "C") if game != "public_goods"
                else (lambda obs: isinstance(obs, int) and obs >= 2))
    refs = reference_rows(args.reference, game) if args.reference else {}

    out_dir = args.out or (group / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)

    def emit(table: Table) -> str:
        (tex_dir / f"{table.key.replace('-', '_')}.tex").write_text(to_latex(table))
        return to_markdown(table)

    md = [header(cells, group, opponents, n_rounds)]
    md.append(emit(per_round_table(cells, opponents, n_rounds)))
    fig = per_round_figure(cells, opponents, n_rounds, out_dir,
                           f"{group.parent.parent.name}_{meta['game_type']}",
                           model=meta["base_model"].rsplit("/", 1)[-1])
    print(f"saved -> {fig}")
    md.append("\n".join([
        "### Figure 1 — cooperation per round", "",
        f"![P(C) per round per opponent]({fig.relative_to(out_dir)})", "",
        "One panel per opponent, one line per policy (base dashed grey). "
        "An alternating line is the repair-retaliate 2-cycle; a rising "
        "flat-topped line is absorption into cooperation.", "", ""]))
    pgg = game == "public_goods"
    reactive = "noisy_conditional" if pgg else "tit_for_tat"
    fig2 = stacked_figure(cells, reactive, n_rounds, pgg, out_dir,
                          f"{group.parent.parent.name}_{game}")
    print(f"saved -> {fig2}")
    md.append("\n".join([
        "### Figure 2 — joint-outcome composition per round", "",
        f"![outcome shares vs {reactive}]({fig2.relative_to(out_dir)})", "",
        f"Vs `{reactive}` (the reactive opponent), each bar splits that "
        "round's episodes by joint outcome. Absorption = one band taking "
        "over; the repair-retaliate cycle = the exploiting and suckered "
        "bands swapping between rounds.", "", ""]))
    md.append(emit(live_state_table(cells, refs)))
    md.append(emit(outcomes_table(cells, opponents, n_rounds, coop_obs)))
    md.append(emit(trace_table(cells, args.principle)))
    npath = write_numbers(cells, opponents, n_rounds, coop_obs, refs,
                          out_dir, game, args.macro_prefix)
    print(f"saved -> {npath}")
    path = out_dir / f"results_{group.name}.md"
    path.write_text("\n".join(md))
    print(f"saved -> {path}")
    return path
