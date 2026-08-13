"""Publication-ready tables (booktabs LaTeX + Markdown) for the
single-turn teacher-signal screen.

Builds seven tables from eval cells (see summarize_eval_cells.py for the
raw cross-arm dumps this condenses):

    state_cooperation   P(C | agent_prev, opp_prev) per game x value x
                        representation, with the opponent-conditioning
                        gap (the surface-robust value signature)
    probe_b_answer      probe-B answer_delta per state (training-signal
                        ground truth), sign-test significance in bold
    probe_b_token       2b/2c: token_delta and token_jsd off the same
                        traces, one table each so the measure's
                        definition sits above its numbers (2c is SDPO's
                        step-0 per-token loss; unsigned, so it sizes the
                        signal where 2 gives its direction)
    robustness          fixed vs surface-randomized presentation:
                        cooperation level and conditioning gap, plus 3b
                        re-cut by state with the own-move gap D_SELF
    slices              per-facet slices within randomized cells
                        (positional-shortcut baseline), split into 4a
                        (cooperation level) and 4b (conditioning gap)

Cells are classified by metadata, so passing the screen group and the
robustness group together produces all seven; duplicate (game, value,
representation, presentation) cells keep the first occurrence. Each
group is assumed comparability-checked already (summarize_eval_cells).

Outputs land in <first path>/tables/ (override with --out): one .tex per
table plus README.md, the rendered Markdown version with a provenance
header. README.md is the git-tracked analysis doc (the eval_results
gitignore un-ignores exactly that name); the .tex files stay data.
For hand-written interpretation, copy README.md to a per-experiment
analysis doc (e.g. single_turn_screen.md) and annotate the copy —
README.md itself is regenerated wholesale.

Login-node friendly (stdlib only, no torch):
    /usr/bin/python3.11 scripts/analysis/publication_tables.py \
        eval_results/teacher_signal/single_turn_screen \
        eval_results/teacher_signal/pd_presentation_robustness
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from summarize_eval_cells import (  # noqa: E402
    _episode_decisions,
    _facet_extractors,
    _mode_splits,
    discover_run_dirs,
    load_json,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS  # noqa: E402
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402

GAMES = (("prisoners_dilemma", "Prisoner's Dilemma"),
         ("stag_hunt", "Stag Hunt"),
         ("chicken", "Chicken"))
VALUES = (("none", "None (base)"),
          ("deontological", "Deontological"),
          ("deontological+repair", "Deontological + repair"),
          ("utilitarian", "Utilitarian"),
          ("virtue", "Virtue"),
          ("universalization", "Universalization"))
REPRS = (("matrix", "Matrix"), ("prose", "Prose"))
STATES4 = ("CC", "CD", "DC", "DD")
PROBE_STATES = ("first", "CC", "CD", "DC", "DD")
MISSING = "--"


# Gap statistics. Every gap in the document carries a subscript naming
# what it measures, so no two are called "Δ": D_OPP is the within-cell
# opponent-conditioning gap, D_SURF its change under surface
# randomization. (The analysis doc adds a third, Δ_rec, the recovery gap
# splitting the two opponent-defected states.)
D_OPP = "$\\Delta_{opp}$"
D_SURF = "$\\Delta_{surf}$"
D_SELF = "$\\Delta_{self}$"


def game_note(game: str) -> str:
    """Strategic description plus that game's fixed eval payoffs and the
    best reply against the random opponent, for the Table 1 panel that
    game's numbers sit in. Payoffs are read from environment.py rather
    than restated, so the prose cannot drift from what was played."""
    p = FIXED_PAYOFFS[game]
    ev_c, ev_d = (p["R"] + p["S"]) / 2, (p["T"] + p["P"]) / 2
    best = ("defect" if ev_d > ev_c else "cooperate" if ev_c > ev_d
            else "indifferent")
    return (f"{GAME_NOTES[game]} Payoffs T = {p['T']}, R = {p['R']}, "
            f"P = {p['P']}, S = {p['S']}. Against the random opponent "
            f"used here, E[C] = {ev_c:g} and E[D] = {ev_d:g}, so the "
            f"best reply is to {best}.")


def state_label(s: str) -> str:
    """'CD' -> 'C$_A$D$_O$': previous moves subscripted A (agent) and
    O (opponent). LaTeX gets real math subscripts; plain() maps them to
    <sub><small> HTML, which renders as a true below-baseline subscript
    a notch smaller than the letter it indexes (Unicode has no subscript
    capitals, and its subscript glyphs are illegibly small)."""
    return f"{s[0]}$_A${s[1]}$_O$"


# ------------------------------------------------------------ table model

@dataclass
class Cell:
    text: str
    bold: bool = False
    marker: str = ""                           # superscript, e.g. "*"


@dataclass
class Table:
    key: str
    title: str
    caption: str
    stub: str                                  # header of the row-label column
    col_groups: List[Tuple[str, List[str]]]    # (group title, column names)
    panels: List[Tuple[Optional[str], List[Tuple[str, List[Cell]]]]]
    notes: List[str] = field(default_factory=list)
    # Markdown-only: panel title -> paragraph printed between the panel
    # heading and its grid, so a reader meets the game (or the metric)
    # right where its numbers are. LaTeX has no clean way to set a
    # paragraph inside a tabular, and in a paper this prose belongs in
    # the body text, so to_latex ignores it -- the .tex files stay data.
    panel_notes: Dict[str, str] = field(default_factory=dict)
    # Markdown-only: collapse each column group into ONE column whose cells
    # join the group's values with a divider ("94 | 95"), so paired
    # matrix/prose entries sit side by side. LaTeX keeps real subcolumns.
    pair_groups: bool = False


def to_latex(t: Table) -> str:
    ncols = sum(len(cols) for _, cols in t.col_groups)
    lines = [
        "% Requires \\usepackage{booktabs} in the preamble.",
        "\\begin{table}[t]",
        "\\centering",
        f"\\caption{{{t.caption}}}",
        f"\\label{{tab:{t.key}}}",
        "\\small",
        "\\setlength{\\tabcolsep}{3.5pt}",
        "\\begin{tabular}{l" + "r" * ncols + "}",
        "\\toprule",
    ]
    if any(title for title, _ in t.col_groups):
        header = [""]
        rules, start = [], 2
        for title, cols in t.col_groups:
            header.append(f"\\multicolumn{{{len(cols)}}}{{c}}{{{title}}}")
            end = start + len(cols) - 1
            if title:
                rules.append(f"\\cmidrule(lr){{{start}-{end}}}")
            start = end + 1
        lines.append(" & ".join(header) + " \\\\")
        lines.append("".join(rules))
    names = [t.stub] + [c for _, cols in t.col_groups for c in cols]
    lines.append(" & ".join(names) + " \\\\")
    lines.append("\\midrule")
    for i, (panel, rows) in enumerate(t.panels):
        if i:
            lines.append("\\addlinespace")
        if panel:
            lines.append(f"\\multicolumn{{{ncols + 1}}}{{l}}"
                         f"{{\\emph{{{panel}}}}} \\\\")
        for label, cells in rows:
            rendered = [(f"\\textbf{{{c.text}}}" if c.bold else c.text)
                        + (f"\\textsuperscript{{{c.marker}}}"
                           if c.marker else "")
                        for c in cells]
            lines.append(" & ".join([label] + rendered) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    if t.notes:
        lines.append("% " + " ".join(t.notes))
    return "\n".join(lines) + "\n"


# LaTeX -> plain Unicode/HTML, so the Markdown renders in any viewer
# (order matters: subscripted deltas must precede the bare one).
_MD_SUBS = (
    ("$\\Delta_{opp}$", "Δ<sub><small>opp</small></sub>"),
    ("$\\Delta_{surf}$", "Δ<sub><small>surf</small></sub>"),
    ("$\\Delta_{self}$", "Δ<sub><small>self</small></sub>"),
    ("$\\Delta$", "Δ"), ("\\%", "%"),
    ("$\\mid$", "|"),
    ("$_A$", "<sub><small>A</small></sub>"),
    ("$_O$", "<sub><small>O</small></sub>"),
    ("$-$", "−"), ("$\\to$", "→"), ("$\\pm$", "±"), ("$T=", "T = "),
    ("\\times", "×"), ("$", ""),
    ("`", "'"), ("s.e.\\", "s.e."), ("\\leq", "≤"), ("opp.\\", "opp."),
    ("vs.\\", "vs."), (" -- ", " — "), ("\\ ", " "), ("\\Delta", "Δ"),
    ("\\approx", "≈"), ("~", " "),
)


def plain(s: str) -> str:
    for latex, text in _MD_SUBS:
        s = s.replace(latex, text)
    return s


def _md_cell(c: Cell) -> str:
    text = ("—" if c.text == MISSING
            else f"**{c.text}**" if c.bold else c.text)
    return text + (f"\\{c.marker}" if c.marker else "")


def to_markdown(t: Table) -> str:
    if t.pair_groups:
        names = [plain(t.stub)] + [plain(title or cols[0])
                                   for title, cols in t.col_groups]
    else:
        names = [plain(t.stub)] + [plain(f"{title} {c}" if title else c)
                                   for title, cols in t.col_groups
                                   for c in cols]
    names = [n.replace("|", "\\|") for n in names]   # bare | breaks the grid
    out = [f"### {plain(t.title)}", "", plain(t.caption), ""]
    for panel, rows in t.panels:
        if panel:
            out += [f"**{panel}**", ""]
            if t.panel_notes.get(panel):
                out += [plain(t.panel_notes[panel]), ""]
        out.append("| " + " | ".join(names) + " |")
        out.append("|" + "---|" * len(names))
        for label, cells in rows:
            if t.pair_groups:
                rendered, i = [], 0
                for _, cols in t.col_groups:
                    group = cells[i:i + len(cols)]
                    i += len(cols)
                    rendered.append(" \\| ".join(map(_md_cell, group)))
            else:
                rendered = [_md_cell(c) for c in cells]
            out.append("| " + " | ".join([label] + rendered) + " |")
        out.append("")
    out += [f"*{plain(n)}*" for n in t.notes] + [""]
    return "\n".join(out)


# ------------------------------------------------------------ data access

CellKey = Tuple[str, str, str, str]  # (game, value, representation, mode)


def collect_cells(paths: List[Path]) -> Dict[CellKey, Path]:
    """First occurrence wins: the robustness group re-runs the screen's
    fixed PD cells, and the screen (listed first) is the canonical one."""
    cells: Dict[CellKey, Path] = {}
    for run_dir in discover_run_dirs(paths):
        meta = (load_json(run_dir, "behavioral.json") or {}).get("metadata")
        if meta is None:
            continue
        presentation = meta.get("eval_presentation") or {}
        mode = ("randomized" if any(v != "fixed"
                                    for v in presentation.values())
                else "fixed")
        key = (meta["game_type"], meta["moral_value"],
               meta["representation"], mode)
        cells.setdefault(key, run_dir)
    return cells


def behavioral(run_dir: Path) -> Dict:
    """Single-opponent block (these sweeps run vs random only)."""
    opponents = load_json(run_dir, "behavioral.json")["opponents"]
    if len(opponents) != 1:
        raise SystemExit(f"{run_dir.name}: expected 1 opponent, "
                         f"got {len(opponents)}")
    return opponents[0]


def pct(p: float) -> str:
    return f"{round(100 * p)}"


def gap_pp(block: Dict) -> int:
    return round(100 * (block["cond_given_opp_c"]["p_C"]
                        - block["cond_given_opp_d"]["p_C"]))


def gap_self_pp(block: Dict) -> int:
    """Own-move counterpart of gap_pp: mean P(C) after the agent's own D
    minus mean P(C) after its own C. Same four cells as D_OPP, sliced on
    the agent's previous move instead of the opponent's. Purely
    descriptive -- the history is fabricated and each episode is one
    independent decision, so this measures a response to a *stated*
    prior move, not alternation over time."""
    sc = block["state_conditioning"]
    def after(own: str) -> float:
        return sum(sc[f"({own},{opp})"]["p_C"] for opp in "CD") / 2
    return round(100 * (after("D") - after("C")))


def sign_test_p(c_ward: int, d_ward: int) -> float:
    """Two-sided binomial sign test on the C-ward/D-ward trace split."""
    n = c_ward + d_ward
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(c_ward, d_ward) + 1))
    return min(1.0, 2 * tail / 2 ** n)


def shared_meta(cells: Dict[CellKey, Path]) -> Dict:
    return load_json(next(iter(cells.values())), "behavioral.json")["metadata"]


# --------------------------------------------------------------- builders

def state_cooperation_table(cells: Dict[CellKey, Path]) -> Table:
    meta = shared_meta(cells)
    panels = []
    for game, game_name in GAMES:
        # (value, repr) -> [p_C per state..., gap]; columns are bolded at
        # their per-column max across the moral values (baseline excluded).
        stats: Dict[Tuple[str, str], List[float]] = {}
        for value, _ in VALUES:
            for repr_, _ in REPRS:
                run_dir = cells.get((game, value, repr_, "fixed"))
                if run_dir is None:
                    continue
                block = behavioral(run_dir)
                sc = block["state_conditioning"]
                stats[(value, repr_)] = (
                    [sc[f"({s[0]},{s[1]})"]["p_C"] for s in STATES4]
                    + [gap_pp(block)])
        col_max = {
            (i, repr_): max(vals[i] for (v, r), vals in stats.items()
                            if r == repr_ and v != "none")
            for i in range(5) for repr_, _ in REPRS
        }
        rows = []
        for value, value_name in VALUES:
            row: List[Cell] = []
            for i in range(5):
                for repr_, _ in REPRS:
                    vals = stats.get((value, repr_))
                    if vals is None:
                        row.append(Cell(MISSING))
                        continue
                    text = (f"{vals[i]:+d}" if i == 4 else pct(vals[i]))
                    row.append(Cell(text, bold=(value != "none"
                                                and vals[i]
                                                == col_max[(i, repr_)])))
            rows.append((value_name, row))
        panels.append((game_name, rows))
    model = meta["base_model"].rsplit("/", 1)[-1]
    return Table(
        key="state-cooperation",
        title="Table 1 — behavioral: state-conditioned cooperation",
        caption=(
            f"Cooperation rate (\\%) of {model} (the agent) by fabricated "
            "previous state. Each panel opens with its game's structure "
            "and the fixed eval payoffs it was played with, quoted from "
            "game/environment.py (FIXED\\_PAYOFFS, Tennant-matching): "
            "T = temptation (defect on a cooperator), R = reward (mutual "
            "cooperation), P = punishment (mutual defection), S = sucker "
            "(cooperate against a defector). Matrix/prose side by side, fixed "
            f"presentation, $T={meta['eval_temperature']}$, "
            f"{meta['num_episodes']} episodes per cell = 100 decisions "
            "per state, so each percentage carries a binomial standard "
            "error of at most 5 points (largest at 50\\%, smaller near "
            "0 or 100) and two cells differing by less than "
            "$\\approx$14 points are not distinguishable. "
            f"{D_OPP} = P(C$\\mid$C$_O$) $-$ P(C$\\mid$D$_O$), in "
            "percentage points: how much more the agent cooperates "
            "after the opponent cooperated than after it defected. "
            "Read it as a tendency, not a strategy: a large value "
            "shows cooperation covarying strongly with the opponent's "
            "last move, though one round of history cannot distinguish "
            "reciprocating from copying it. A small value is not "
            "evidence of no effect -- the s.e.\\ is $\\approx$5 points, "
            "and opposite effects in the two own-move strata cancel, so "
            "a gap near zero can hide large within-stratum swings "
            "(Table~3b slices on the own move). Presentation effects "
            "are Table~3. Bold: column-wise "
            "maximum across the moral values (baseline excluded)."),
        stub="Value (matrix $\\mid$ prose)",
        col_groups=[(f"P(C$\\mid${state_label(s)})", ["mat.", "prose"])
                    for s in STATES4]
        + [(D_OPP, ["mat.", "prose"])],
        panels=panels,
        panel_notes={game_name: game_note(game)
                     for game, game_name in GAMES},
        pair_groups=True,
    )


def probe_b_table(cells: Dict[CellKey, Path],
                  alpha: float = 0.01) -> Table:
    n_traces = None
    panels = []
    for game, game_name in GAMES:
        # (value, repr) -> state -> (mean, significant); bold = largest
        # |mean| across values per (state, repr) = the strongest signal.
        stats: Dict[Tuple[str, str], Dict[str, Tuple[float, bool]]] = {}
        for value, _ in VALUES:
            for repr_, _ in REPRS:
                run_dir = cells.get((game, value, repr_, "fixed"))
                probe = (load_json(run_dir, "probe_b.json")
                         if run_dir else None)
                if probe is None:
                    continue
                splits = _mode_splits(run_dir)
                per_state = {}
                for state in PROBE_STATES:
                    s = probe["probe_b"][state]["answer_delta"]
                    n_traces = n_traces or s["n"]
                    per_state[state] = (s["mean"],
                                        sign_test_p(*splits[state]) < alpha)
                stats[(value, repr_)] = per_state
        if not stats:
            continue
        col_max = {
            (state, repr_): max(abs(per_state[state][0])
                                for (v, r), per_state in stats.items()
                                if r == repr_)
            for state in PROBE_STATES for repr_, _ in REPRS
        }
        rows = []
        for value, value_name in VALUES:
            if not any((value, r) in stats for r, _ in REPRS):
                continue
            row: List[Cell] = []
            for state in PROBE_STATES:
                for repr_, _ in REPRS:
                    per_state = stats.get((value, repr_))
                    if per_state is None:
                        row.append(Cell(MISSING))
                        continue
                    mean, significant = per_state[state]
                    row.append(Cell(
                        f"{mean:+.2f}",
                        bold=abs(mean) == col_max[(state, repr_)],
                        marker="*" if significant else ""))
            rows.append((value_name, row))
        panels.append((game_name, rows))
    return Table(
        key="probe-b-answer-delta",
        title="Table 2 — probe_b: answer shift (teacher $-$ student)",
        caption=(
            "How far the moral text moves the final answer, with the "
            "reasoning held fixed. For one trace r, let "
            "L(p, r) = log P(coop label | p, r) $-$ "
            "log P(defect label | p, r) be the answer log-odds after "
            f"prompt p. The entry is the mean over the N = {n_traces} "
            "traces of [ L(teacher, r) $-$ L(student, r) ], where "
            "student is the plain prompt and teacher the same prompt "
            "with the moral wording prepended. Traces are sampled from "
            "the student prompt ($T = 0.7$, which is what SDPO scores) "
            "and cut at their final Action: marker, so both sides score "
            "identical reasoning and only the prepended text differs. "
            "Natural log-odds: $+0.7$ doubles the odds of cooperating, "
            "$+2.3$ is $10\\times$, $+3.4$ is $30\\times$; negative is "
            "defect-ward. A mean over traces, not over tokens -- the "
            "per-token quantities off these same traces are Tables 2b "
            "(token\\_delta) and 2c (token\\_jsd). "
            "Matrix/prose side by side, fixed presentation; `first' is "
            "the history-free state. Bold: largest magnitude across the "
            "moral values for that state and representation. *: "
            "two-sided sign test on the C-ward/D-ward trace split, "
            f"$p < {alpha}$."),
        stub="Value (matrix $\\mid$ prose)",
        col_groups=[(s if s == "first" else state_label(s),
                     ["mat.", "prose"]) for s in PROBE_STATES],
        panels=panels,
        pair_groups=True,
    )


ROBUSTNESS_VALUES = ("none", "deontological")


SHARED_TRACE_NOTE = (
    "Computed on the same 32 student traces as Table 2, but "
    "teacher-forcing the WHOLE trace (reasoning and answer) after the "
    "student prompt and after the teacher prompt, rather than only the "
    "answer label. Matrix/prose side by side, fixed presentation; "
    "`first' is the history-free state.")

TOKEN_TABLES = (
    ("token_delta", "token\\_delta", "{:+.3f}", "2b",
     "Mean per-token logprob of the trace under the teacher prompt "
     "minus under the student prompt: how much less (or more) typical "
     "the student's own reasoning looks once the moral text is "
     "prepended. Read the magnitude, not the sign -- it is negative "
     "almost everywhere by construction, because the trace was sampled "
     "from the student prompt and any added context lowers its "
     "likelihood. A diagnostic, not the training objective."),
    ("token_jsd", "token\\_jsd", "{:.3f}", "2c",
     "The generalized Jensen-Shannon divergence between the two "
     "full-vocab next-token distributions -- student-prompt s and "
     "teacher-prompt t -- averaged over the trace's token positions: "
     "with m = (1-a)s + at, JSD = (1-a)KL(s||m) + a KL(t||m), the "
     "a = 0 and a = 1 branches degenerating to plain KL. This mirrors "
     "SDPO's compute\\_self\\_distillation\\_loss, so it IS the "
     "per-token loss SDPO computes, evaluated at step 0 -- the size of "
     "the gradient signal the moral text supplies before any training. "
     "What it cannot do is point: a divergence is non-negative, so it "
     "says how far the moral text moves the policy, never which way -- "
     "a wording can score the same at two states whose signed "
     "answer\\_delta (Table 2) points in opposite directions. Nor does "
     "magnitude predict efficacy: a large divergence can accompany a "
     "weak behavioral pull. Because it averages over the whole "
     "vocabulary and the whole trace, a wording can score high by "
     "rewording the reasoning without changing the decision."),
)


def probe_b_token_tables(cells: Dict[CellKey, Path]) -> List[Table]:
    """Tables 2b/2c — the trace-level companions to Table 2's
    answer_delta, one table per metric so each measure's definition sits
    directly above its own numbers. Rows are the moral values, columns
    the states with matrix/prose paired, mirroring Table 2 so the three
    read against each other."""
    tables = []
    for metric, metric_name, fmt, number, definition in TOKEN_TABLES:
        alpha, panels = None, []
        for game, game_name in GAMES:
            rows = []
            for value, value_name in VALUES:
                row: List[Cell] = []
                seen = False
                for state in PROBE_STATES:
                    for repr_, _ in REPRS:
                        run_dir = cells.get((game, value, repr_, "fixed"))
                        probe = (load_json(run_dir, "probe_b.json")
                                 if run_dir else None)
                        if probe is None:
                            row.append(Cell(MISSING))
                            continue
                        alpha = alpha or (probe["metadata"]
                                          .get("distillation_alpha"))
                        mean = probe["probe_b"][state][metric]["mean"]
                        seen = True
                        row.append(Cell(MISSING if mean is None
                                        else fmt.format(mean)))
                if seen:
                    rows.append((value_name, row))
            if rows:
                panels.append((game_name, rows))
        if not panels:
            continue
        tables.append(Table(
            key=f"probe-b-{metric.replace('_', '-')}",
            title=f"Table {number} — probe\\_b: {metric_name}",
            caption=(definition
                     + (f" Here a = {alpha}." if metric == "token_jsd"
                        and alpha is not None else "")
                     + " " + SHARED_TRACE_NOTE),
            stub="Value (matrix $\\mid$ prose)",
            col_groups=[(s if s == "first" else state_label(s),
                         ["mat.", "prose"]) for s in PROBE_STATES],
            panels=panels,
            pair_groups=True,
        ))
    return tables


def robustness_table(cells: Dict[CellKey, Path]) -> Table:
    rows = []
    for value, value_name in VALUES:
        if value not in ROBUSTNESS_VALUES:
            continue
        for repr_, repr_name in REPRS:
            pair = {mode: cells.get(("prisoners_dilemma", value,
                                     repr_, mode))
                    for mode in ("fixed", "randomized")}
            if None in pair.values():
                continue
            row: List[Cell] = []
            gaps = {}
            for mode in ("fixed", "randomized"):
                block = behavioral(pair[mode])
                gaps[mode] = gap_pp(block)
                row += [Cell(pct(block["cooperation_rate"])),
                        Cell(pct(block["cond_given_opp_c"]["p_C"])),
                        Cell(pct(block["cond_given_opp_d"]["p_C"])),
                        Cell(f"{gaps[mode]:+d}")]
            row.append(Cell(f"{gaps['randomized'] - gaps['fixed']:+d}"))
            rows.append((f"{value_name}, {repr_name.lower()}", row))
    return Table(
        key="presentation-robustness",
        title="Table 3 — pd_presentation_robustness: fixed vs.\\ "
              "randomized presentation (Prisoner's Dilemma)",
        caption=(
            "Fixed vs.\\ surface-randomized presentation (labels, layout, "
            "label order, role; payoffs fixed). The four axes re-render "
            "an identical game, so any difference is attributable to how "
            f"the payoff block is read. The conditioning gap {D_OPP} is "
            "the more presentation-stable statistic by construction, "
            "because a shift common to both conditioning arms cancels in "
            "the difference, while the cooperation level does not. "
            f"Measured for none and deontological in PD only. {D_SURF} = "
            f"the change in {D_OPP} under randomization."),
        stub="Value, repr.",
        col_groups=[("Fixed", ["P(C)", "P(C$\\mid$C$_O$)",
                               "P(C$\\mid$D$_O$)", D_OPP]),
                    ("Randomized", ["P(C)", "P(C$\\mid$C$_O$)",
                                    "P(C$\\mid$D$_O$)", D_OPP]),
                    ("", [D_SURF])],
        panels=[(None, rows)],
    )


def robustness_states_table(cells: Dict[CellKey, Path]) -> Optional[Table]:
    """Table 3 re-cut: fixed vs randomized as ROWS (one panel per value x
    representation, so the pair sits vertically adjacent) and the pooled
    conditionals expanded into the four fabricated states. Adds D_SELF
    next to D_OPP, which is the point of the table: the two arms load on
    different axes, and the loading -- not the level -- is what survives
    surface randomization."""
    # Bold the dominant axis so the orthogonality of the two arms reads
    # straight off the columns -- but only once a gap clears the noise
    # floor, or near-zero-vs-near-zero cells get a spurious winner.
    def dominant(a: int, b: int) -> bool:
        return abs(a) >= 10 and abs(a) > abs(b)

    panels = []
    for value, value_name in VALUES:
        if value not in ROBUSTNESS_VALUES:
            continue
        rows = []
        for repr_, repr_name in REPRS:
            for mode in ("fixed", "randomized"):
                run_dir = cells.get(("prisoners_dilemma", value,
                                     repr_, mode))
                if run_dir is None:
                    continue
                block = behavioral(run_dir)
                sc = block["state_conditioning"]
                self_gap, opp_gap = gap_self_pp(block), gap_pp(block)
                rows.append((f"{repr_name.lower()}, {mode}", [
                    Cell(pct(block["cooperation_rate"])),
                    *(Cell(pct(sc[f"({s[0]},{s[1]})"]["p_C"]))
                      for s in STATES4),
                    Cell(f"{self_gap:+d}", bold=dominant(self_gap, opp_gap)),
                    Cell(f"{opp_gap:+d}", bold=dominant(opp_gap, self_gap)),
                ]))
        if rows:
            panels.append((value_name, rows))
    if not panels:
        return None
    return Table(
        key="presentation-robustness-states",
        title="Table 3b — pd\\_presentation\\_robustness: fixed vs.\\ "
              "randomized, by state (Prisoner's Dilemma)",
        caption=(
            "Table 3 with the presentation modes as rows and the pooled "
            "conditionals expanded into the four fabricated states. "
            f"{D_SELF} = mean P(C) after the agent's own D minus mean "
            f"P(C) after its own C, the own-move counterpart of {D_OPP}: "
            "the same four cells, sliced on the agent's previous move "
            "instead of the opponent's. Bold marks the larger of the two "
            "gaps when it clears 10 points, i.e.\\ the axis that arm "
            "loads on where there is one. The comparison to make is "
            "whether randomization moves the level, the loading, or "
            "both: an arm whose loading survives is reading the payoff "
            "structure rather than the surface. Fixed cells are the "
            "screen group's, as in "
            "Table 3. Caution: the history is fabricated and each "
            f"episode is a single independent decision, so {D_SELF} "
            "describes a response to a stated prior move, not "
            "alternation over time."),
        stub="Repr., presentation",
        col_groups=[("", ["P(C)", *(f"P(C$\\mid${state_label(s)})"
                                    for s in STATES4),
                          D_SELF, D_OPP])],
        panels=panels,
    )


def _slice_level_stats(
        run_dir: Path) -> Dict[Tuple[str, str], Tuple[str, str, int]]:
    data = load_json(run_dir, "behavioral.json")
    episodes = [ep for block in data["opponents"]
                for ep in block.get("episode_moves", [])
                if "presentation" in ep]
    facets = _facet_extractors([ep["presentation"] for ep in episodes])
    stats = {}
    for facet, fn in facets.items():
        by_level: Dict[str, List[Dict]] = {}
        for i, ep in enumerate(episodes):
            by_level.setdefault(fn(ep["presentation"]), []).extend(
                _episode_decisions(ep, i, data["metadata"]))
        for level, decisions in by_level.items():
            legal = [d for d in decisions if d["move"] in ("C", "D")]
            cond = {prev: [d["move"] == "C" for d in legal
                           if d["opp_prev"] == prev]
                    for prev in ("C", "D")}
            p_c = sum(d["move"] == "C" for d in legal) / len(legal)
            gap = round(100 * (sum(cond["C"]) / len(cond["C"])
                               - sum(cond["D"]) / len(cond["D"])))
            stats[(facet, level)] = (pct(p_c), f"{gap:+d}",
                                     len(by_level[level]))
    return stats


def slices_tables(cells: Dict[CellKey, Path]) -> List[Table]:
    """4a (cooperation level) and 4b (conditioning gap), one column
    group per value with matrix/prose paired, mirroring Tables 1-2."""
    groups: List[Tuple[str, List[Dict]]] = []
    for value, value_name in VALUES:
        if value not in ROBUSTNESS_VALUES:
            continue
        run_dirs = [cells.get(("prisoners_dilemma", value, repr_,
                               "randomized"))
                    for repr_, _ in REPRS]
        if all(d is None for d in run_dirs):
            continue
        groups.append((value_name,
                       [_slice_level_stats(d) if d else {}
                        for d in run_dirs]))
    if not groups:
        return []
    ref = next(stats for _, per_repr in groups for stats in per_repr
               if stats)
    facet_order = list(dict.fromkeys(f for f, _ in ref))
    keys = sorted(ref, key=lambda k: (facet_order.index(k[0]), k[1]))

    def build(idx: int, key: str, title: str, caption: str) -> Table:
        rows = []
        for facet, level in keys:
            row = [Cell(str(ref[(facet, level)][2]))]
            for _, per_repr in groups:
                row += [Cell(stats.get((facet, level),
                                       (MISSING, MISSING, 0))[idx])
                        for stats in per_repr]
            rows.append((f"{facet}: {level}".replace("_", " "), row))
        return Table(
            key=key, title=title, caption=caption,
            stub="Facet: level (matrix $\\mid$ prose)",
            col_groups=[("", ["$n$"])] + [(name, ["Matrix", "Prose"])
                                          for name, _ in groups],
            panels=[(None, rows)],
            pair_groups=True,
        )

    shared = ("sliced by presentation facet within the "
              "surface-randomized PD cells. Level counts $n$ are shared "
              "across cells (same presentation-sampling seed).")
    return [
        build(
            0, "presentation-slices-coop",
            "Table 4a — pd_presentation_robustness slices: cooperation "
            "level by facet (Prisoner's Dilemma, randomized cells)",
            f"Cooperation rate (\\%) {shared} Layout is the dominant "
            "positional shortcut for the cooperation level."),
        build(
            1, "presentation-slices-gap",
            "Table 4b — pd_presentation_robustness slices: conditioning "
            "gap by facet (Prisoner's Dilemma, randomized cells)",
            f"Conditioning gap {D_OPP} = P(C$\\mid$C$_O$) $-$ "
            f"P(C$\\mid$D$_O$) (percentage points) {shared} The "
            "deontological gap stays positive in every slice while the "
            "baseline gap wobbles around zero."),
    ]


def readme_header(cells: Dict[CellKey, Path], paths: List[Path]) -> str:
    meta = shared_meta(cells)
    dates = sorted({(load_json(d, "behavioral.json")["metadata"]
                     .get("timestamp") or "")[:10]
                    for d in cells.values()})
    groups = ", ".join(dict.fromkeys(p.name for p in paths))
    return "\n".join([
        "# Single-turn screen: results tables",
        "",
        f"{meta['base_model'].rsplit('/', 1)[-1]} ({meta['model_type']}), "
        f"protocol `{meta['protocol']}` (fabricated history, balanced "
        f"states), T = {meta['eval_temperature']}, "
        f"{meta['num_episodes']} episodes/cell (100 per state), "
        f"vs. random opponent. Groups: {groups} "
        f"({len(cells)} cells, runs {' / '.join(d for d in dates if d)}).",
        "",
        "States are the fabricated previous round, subscripted A "
        f"(agent) and O (opponent): {plain(state_label('CD'))} = agent "
        "cooperated, opponent defected; C = cooperate. Illegal moves are a third category, "
        "never folded into D. Table sections are named after the "
        "experiment that produced them (behavioral, probe_b, "
        "pd_presentation_robustness). Every gap statistic carries a "
        f"subscript naming what it measures: {plain(D_OPP)} = "
        "opponent-conditioning gap, "
        f"{plain(D_SURF)} = its change under surface randomization. "
        "Rendered from the same cell data as the LaTeX sources in this "
        "directory by `scripts/analysis/publication_tables.py`.",
        "", "",
    ])


# Structure / equilibrium prose per game. The payoff numbers and the
# expected values below are computed from FIXED_PAYOFFS, so only the
# game-theoretic reading lives here.
GAME_NOTES = {
    "prisoners_dilemma": (
        "Defection strictly dominates: it pays more whatever the "
        "opponent does (T > R and P > S). The unique Nash equilibrium "
        "is mutual defection, which pays both players less than mutual "
        "cooperation would — cooperating means overriding the dominant "
        "strategy."),
    "stag_hunt": (
        "No dominant strategy; a trust/coordination problem. Two pure "
        "equilibria: mutual cooperation (payoff-dominant, the joint "
        "best) and mutual defection (the safe choice — defecting "
        "guarantees P, cooperating risks S). Cooperation is the best "
        "reply once the opponent is believed to cooperate with "
        "probability above the mixed-equilibrium threshold."),
    "chicken": (
        "No dominant strategy; an anti-coordination game. The two pure "
        "equilibria are asymmetric (one player yields, the other "
        "exploits); mutual defection is the worst joint outcome, and "
        "mutual cooperation is stable for neither player. The best "
        "reply is the opposite of what the opponent is expected to "
        "do."),
}


def moral_values_section() -> str:
    """Verbatim teacher texts, quoted from the single source of truth
    (game/moral_values.py) so the tables read without the codebase."""
    lines = [
        "### Moral value prompts",
        "",
        "Teacher texts of the sweep's arms, verbatim from "
        "`src/moralgym_verl/game/moral_values.py` (the exact string "
        "prepended to the prompt at eval time). `none` adds no text; "
        "`+`-composites join their parts as separate paragraphs.",
        "",
    ]
    for value, value_name in VALUES:
        if value == "none":
            continue
        lines += [f"**{value_name}** (`{value}`)", ""]
        lines += ["> " + line if line else ">"
                  for line in get_moral_value(value).splitlines()]
        lines.append("")
    return "\n".join(lines + [""])


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publication tables for the single-turn screen")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="Eval-group directories (screen first, then "
                             "robustness) or explicit run directories.")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: "
                             "<first path>/tables).")
    args = parser.parse_args()

    cells = collect_cells(args.paths)
    builders: List[Callable[[Dict[CellKey, Path]],
                            Union[Table, List[Table], None]]] = [
        state_cooperation_table, probe_b_table, probe_b_token_tables,
        robustness_table,
        robustness_states_table, slices_tables,
    ]
    tables: List[Table] = []
    for build in builders:
        built = build(cells)
        if built is not None:
            tables.extend(built if isinstance(built, list) else [built])

    out_dir = args.out or (args.paths[0] / "tables")
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = [readme_header(cells, args.paths),
                moral_values_section()]
    for table in tables:
        tex_path = out_dir / f"{table.key.replace('-', '_')}.tex"
        tex_path.write_text(to_latex(table))
        markdown.append(to_markdown(table))
        print(f"saved -> {tex_path}")
    md_path = out_dir / "README.md"
    md_path.write_text("\n".join(markdown))
    print(f"saved -> {md_path}\n")
    print("\n".join(markdown[1:]), end="")


if __name__ == "__main__":
    main()
