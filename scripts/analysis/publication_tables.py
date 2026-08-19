"""Publication-ready tables (booktabs LaTeX + Markdown) for the
single-turn teacher-signal screen.

Builds the results tables from eval cells:

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
    robustness          Table 3: cooperation by state under re-rendered
                        presentation, one panel per value — fixed and
                        all-four-surface endpoints
                        (pd_presentation_robustness) plus the per-axis
                        arms and payoff content control
                        (pd_randomization_ablation) when those groups
                        are passed

Cells are classified by metadata, so passing the screen, robustness and
ablation groups together produces all of them; duplicate (game, value,
representation, presentation) cells keep the first occurrence. Each
group is assumed comparability-checked already.

Outputs land in <first path>/analysis/ (override with --out):
results_<model>.md (the rendered tables with a provenance header) plus
one .tex per table under analysis/tex/. The group layout is then

    <group>/cells/<cell>/         machine-readable per-cell JSON
    <group>/analysis/             everything human-readable
    <group>/sweep_manifest.json   what was launched

The doc is named after the model so one experiment folder can hold
several models' results side by side, and both it and the .tex sources
describe only the experiment and the statistics: no model's findings
appear in another model's captions.

results_<model>.md is regenerated wholesale. For hand-written
interpretation, copy it to analysis.md in the same folder and annotate
the copy; the gitignore tracks both names and nothing else here.

Login-node friendly (stdlib only, no torch):
    /usr/bin/python3.11 scripts/analysis/publication_tables.py \
        eval_results/teacher_signal/single_turn_screen_gemma-2-9b-it \
        eval_results/teacher_signal/pd_presentation_robustness \
        eval_results/teacher_signal/pd_randomization_ablation
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
from eval_cells import (  # noqa: E402
    check_comparability,
    discover_run_dirs,
    load_json,
    mode_splits,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS  # noqa: E402
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402

GAMES = (("prisoners_dilemma", "Prisoner's Dilemma"),
         ("stag_hunt", "Stag Hunt"),
         ("chicken", "Chicken"))
VALUES = (("none", "None (base)"),
          ("deontological", "Deontological"),
          ("deontological+repair", "Deontological + repair"),
          ("deontological+repair+generosity", "Deontological + repair + generosity"),
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


def game_note(game: str) -> str:
    """Strategic description plus that game's fixed eval payoffs and the
    best reply against the random opponent, for the Table 1 panel that
    game's numbers sit in. Payoffs are read from environment.py rather
    than restated, so the prose cannot drift from what was played."""
    p = FIXED_PAYOFFS[game]
    ev_c, ev_d = (p["R"] + p["S"]) / 2, (p["T"] + p["P"]) / 2
    best = ("defect" if ev_d > ev_c else "cooperate" if ev_c > ev_d
            else "indifferent")
    return (f"{GAME_NOTES[game]} Payoffs {p['T']}/{p['R']}/{p['P']}/"
            f"{p['S']} (T/R/P/S); vs the random opponent: {best} "
            f"(E[C] = {ev_c:g}, E[D] = {ev_d:g}).")


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
    ("$_S$", "<sub><small>S</small></sub>"),
    ("$_T$", "<sub><small>T</small></sub>"),
    ("$-$", "−"), ("$\\to$", "→"), ("$\\pm$", "±"), ("$T=", "T = "),
    ("\\ell", "ℓ"),
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

# presentation_spec -> mode. The endpoints keep their historical mode
# names ("fixed"/"randomized", what Tables 3-4 look up); an ablation
# cell's mode is its axis name. Cells from before presentation_spec
# existed (the screen) fall back to the resolved eval_presentation dict.
SPEC_MODES = {"fixed_representation": "fixed",
              "surface_randomization": "randomized"}


def cell_mode(meta: Dict) -> str:
    spec = meta.get("presentation_spec")
    if spec is None:
        presentation = meta.get("eval_presentation") or {}
        spec = ("surface_randomization"
                if any(v != "fixed" for v in presentation.values())
                else "fixed_representation")
    return SPEC_MODES.get(spec, spec)


def collect_cells(paths: List[Path], strict: bool = True) -> Dict[CellKey, Path]:
    """First occurrence wins: the robustness group re-runs the screen's
    fixed PD cells, and the screen (listed first) is the canonical one.

    Each group is comparability-checked on its own first: a cell that
    differs in a setting the sweep never declared as an axis (a different
    token budget, temperature, model) would otherwise be averaged into a
    table as if it belonged there. Checked per path, since two groups may
    legitimately differ on an axis neither declares.
    """
    dirty = [p for p in paths if not check_comparability(discover_run_dirs([p]))]
    if dirty:
        msg = ("cells differ in settings their sweep never declared as an "
               f"axis (see WARNINGs above): {', '.join(p.name for p in dirty)}")
        if strict:
            raise SystemExit(f"ERROR: {msg}\nPass --allow-mixed to build the "
                             f"tables anyway.")
        print(f"WARNING: {msg} -- building anyway (--allow-mixed).")

    cells: Dict[CellKey, Path] = {}
    for run_dir in discover_run_dirs(paths):
        meta = (load_json(run_dir, "behavioral.json") or {}).get("metadata")
        if meta is None:
            continue
        key = (meta["game_type"], meta["moral_value"],
               meta["representation"], cell_mode(meta))
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
            f"Cooperation rate (\\%) of {model} by fabricated previous "
            "state; matrix/prose side by side, fixed presentation, "
            f"$T={meta['eval_temperature']}$, "
            f"{meta['num_episodes']} episodes per cell = 100 decisions "
            "per state (binomial s.e.\\ $\\leq$5 points; differences "
            "under $\\approx$14 points are not distinguishable). "
            f"{D_OPP} = P(C$\\mid$C$_O$) $-$ P(C$\\mid$D$_O$) tells us "
            "how much the agent cooperated when the opponent "
            "cooperated, versus when the opponent defected. A large "
            "positive number indicates reciprocity behaviour."),
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
                splits = mode_splits(run_dir)
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
            "How much the moral wording shifts the final answer, with "
            "the reasoning held fixed. r is a trace: one of "
            f"N = {n_traces} model-generated reasoning chains, sampled "
            "from the plain game prompt and cut at its final Action: "
            "marker. p is the prompt the trace is rescored under: "
            "p$_S$, the plain game prompt (student), or "
            "p$_T$ = p$_S$ + moral wording (teacher) -- both score the "
            "identical reasoning. "
            "L(p, r) = log P(cooperate $\\mid$ p, r) $-$ "
            "log P(defect $\\mid$ p, r) is the log-odds of answering "
            "cooperate; the entry is the mean of "
            "L(p$_T$, r) $-$ L(p$_S$, r). Positive "
            "= the wording pushes the answer "
            "toward cooperate; an entry of $+0.7$ multiplies the odds "
            "of cooperating by exp(0.7) $\\approx$ 2, $+2.3$ by "
            "$\\approx$10. Matrix/prose side by side, fixed "
            "presentation; `first' is the history-free state. *: sign "
            f"test, $p < {alpha}$."),
        stub="Value (matrix $\\mid$ prose)",
        col_groups=[(s if s == "first" else state_label(s),
                     ["mat.", "prose"]) for s in PROBE_STATES],
        panels=panels,
        pair_groups=True,
    )


ROBUSTNESS_VALUES = ("none", "deontological")


TRACE_LEAD = (
    "Same traces r and prompts p$_S$ (student) / p$_T$ (teacher) as "
    "Table 2, but scored over the whole trace (reasoning and answer) "
    "rather than only the answer label.")

TRACE_TAIL = (
    "Matrix/prose side by side, fixed presentation; `first' is the "
    "history-free state.")

TOKEN_TABLES = (
    ("token_delta", "token\\_delta", "{:+.3f}", "2b",
     "$\\ell$(p, r) = mean per-token log-probability of trace r under "
     "prompt p; the entry is the mean over traces of "
     "$\\ell$(p$_T$, r) $-$ $\\ell$(p$_S$, r): how much less typical "
     "the student's own reasoning looks once the moral wording is "
     "prepended. Read the magnitude, not the sign -- it is negative "
     "almost everywhere, because r was sampled under p$_S$ and any "
     "added context lowers its likelihood. A diagnostic, not the "
     "training objective."),
    ("token_jsd", "token\\_jsd", "{:.3f}", "2c",
     "Rescoring r under a prompt yields, at each of its K token "
     "positions, a probability vector over the entire vocabulary (one "
     "entry per token the model could emit next, e.g.\\ 256k for "
     "gemma-2): its prediction of what comes next at that point -- so "
     "each prompt produces a K $\\times$ |V| matrix, one row per "
     "position. s and t are the matching rows at one position under "
     "p$_S$ and p$_T$. The entry is the generalized Jensen-Shannon "
     "divergence "
     "between them, JSD = (1$-$a) KL(s || m) + a KL(t || m) with "
     "mixture m = (1$-$a)s + a t, averaged over the trace's positions: "
     "how much the wording reshapes the model's whole next-token "
     "prediction at each step of the reasoning, not just the "
     "probability of the token actually there (that is 2b). This "
     "is SDPO's per-token loss (compute\\_self\\_distillation\\_loss) "
     "at step 0: the size of the training signal the wording supplies "
     "before any training. Unsigned -- it says how far the wording "
     "moves the policy, not which way -- and magnitude does not "
     "predict behavioral efficacy: a wording can score high by "
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
            caption=(TRACE_LEAD + " " + definition
                     + (f" Here a = {alpha}." if metric == "token_jsd"
                        and alpha is not None else "")
                     + " " + TRACE_TAIL),
            stub="Value (matrix $\\mid$ prose)",
            col_groups=[(s if s == "first" else state_label(s),
                         ["mat.", "prose"]) for s in PROBE_STATES],
            panels=panels,
            pair_groups=True,
        ))
    return tables


# Row order of Table 3's panels: endpoints around the single-axis arms
# (each randomizes ONE surface axis, the other three fixed), the content
# control last. Modes are presentation_spec values via cell_mode().
ABLATION_ARMS = (
    ("fixed", "fixed"),
    ("labels", "labels only"),
    ("layout", "layout only"),
    ("label_order", "label order only"),
    ("role", "role only"),
    ("randomized", "all four (surface)"),
    ("payoffs", "payoffs (content)"),
)


def ablation_states_tables(cells: Dict[CellKey, Path]) -> Optional[Table]:
    """Presentation robustness by state, one panel per value:
    presentation arms as ROWS (fixed, each single randomized axis, all
    four, the payoff content control), matrix/prose paired per column,
    the four fabricated states as columns. The single-axis rows say
    WHICH axis moves the level and the conditioning; an arm whose
    D_OPP survives every row is reading the payoff structure rather
    than the surface."""
    shared_caption = (
        "Measures whether behaviour reads the payoff structure or its "
        "surface rendering: the same PD is re-rendered along one "
        "presentation axis at a time and cooperation by state is "
        "compared against the fixed rendering. A surface-reader moves "
        "when the rendering changes; a payoff-reader only when the "
        "payoffs do. One panel per moral value; rows are the "
        "randomization axes: `fixed' = the screen's fixed-presentation "
        "cell; each `only' row randomizes one surface axis with the "
        "other three fixed; `all four (surface)' draws labels, layout, "
        "label order and role jointly (payoffs fixed); `payoffs' "
        "resamples T, R, P, S preserving the PD ordering (the content "
        "control). Distance from the fixed row = that axis's own "
        "effect; distance from the all-four row = what the remaining "
        f"axes add. {D_OPP} = P(C$\\mid$C$_O$) $-$ P(C$\\mid$D$_O$); "
        f"{D_SURF}(axis) = {D_OPP}(axis) $-$ "
        f"{D_OPP}(fixed). PD only.")

    panels = []
    for value, value_name in VALUES:
        if value not in ROBUSTNESS_VALUES:
            continue
        fixed_gaps = [gap_pp(behavioral(d)) if d is not None else None
                      for d in (cells.get(("prisoners_dilemma", value,
                                           repr_, "fixed"))
                                for repr_, _ in REPRS)]
        rows = []
        for mode, arm_name in ABLATION_ARMS:
            pair = [cells.get(("prisoners_dilemma", value, repr_, mode))
                    for repr_, _ in REPRS]
            if all(d is None for d in pair):
                continue
            row: List[Cell] = [Cell(MISSING)] * (2 * 7)
            for i, run_dir in enumerate(pair):
                if run_dir is None:
                    continue
                block = behavioral(run_dir)
                sc = block["state_conditioning"]
                opp_gap = gap_pp(block)
                stats = [Cell(pct(block["cooperation_rate"])),
                         *(Cell(pct(sc[f"({s[0]},{s[1]})"]["p_C"]))
                           for s in STATES4),
                         Cell(f"{opp_gap:+d}"),
                         Cell(MISSING if mode == "fixed"
                              or fixed_gaps[i] is None
                              else f"{opp_gap - fixed_gaps[i]:+d}")]
                for j, cell in enumerate(stats):
                    row[2 * j + i] = cell
            rows.append((arm_name, row))
        if len(rows) < 2:      # nothing to compare against the fixed row
            continue
        panels.append((value_name, rows))
    if not panels:
        return None
    return Table(
        key="randomization-ablation",
        title="Table 3 — randomization ablation by state "
              "(Prisoner's Dilemma)",
        caption=shared_caption,
        stub="Presentation (matrix $\\mid$ prose)",
        col_groups=[("P(C)", ["mat.", "prose"])]
        + [(f"P(C$\\mid${state_label(s)})", ["mat.", "prose"])
           for s in STATES4]
        + [(D_OPP, ["mat.", "prose"]), (D_SURF, ["mat.", "prose"])],
        panels=panels,
        pair_groups=True,
    )


def model_slug(cells: Dict[CellKey, Path]) -> str:
    """Filename-safe model id, e.g. 'Qwen/Qwen3-8B' -> 'qwen3-8b'. Names
    the output doc so one experiment folder can hold results for more
    than one model without them overwriting each other."""
    return shared_meta(cells)["base_model"].rsplit("/", 1)[-1].lower()


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
        "cooperated, opponent defected; C = cooperate. Illegal moves "
        "are a third category, never folded into D. Generated by "
        "`scripts/analysis/publication_tables.py` (LaTeX sources in "
        "`tex/`).",
        "", "",
    ])


# Structure / equilibrium prose per game. The payoff numbers and the
# expected values below are computed from FIXED_PAYOFFS, so only the
# game-theoretic reading lives here.
GAME_NOTES = {
    "prisoners_dilemma": (
        "Defection strictly dominates (T > R and P > S): cooperating "
        "means overriding the dominant strategy."),
    "stag_hunt": (
        "Trust/coordination game, two equilibria: mutual cooperation "
        "(joint best) vs mutual defection (safe)."),
    "chicken": (
        "Anti-coordination game: the best reply is the opposite of the "
        "opponent's expected move; mutual defection is the worst joint "
        "outcome."),
}


def moral_values_section() -> str:
    """Verbatim teacher texts, quoted from the single source of truth
    (game/moral_values.py) so the tables read without the codebase.
    A composite quotes only the paragraphs it ADDS to components already
    listed above it, so shared text appears once."""
    lines = [
        "### Moral value prompts",
        "",
        "Teacher texts of the sweep's arms, verbatim from "
        "`src/moralgym_verl/game/moral_values.py` (the exact string "
        "prepended to the prompt at eval time). `none` adds no text; "
        "`+`-composites join their parts as separate paragraphs, and "
        "are shown here as only the paragraphs they add to parts "
        "already listed above.",
        "",
    ]
    shown: List[str] = []
    for value, value_name in VALUES:
        if value == "none":
            continue
        parts = value.split("+")
        base = [p for p in parts if p in shown]
        new = [p for p in parts if p not in shown]
        lines += [f"**{value_name}** (`{value}`)", ""]
        if base:
            lines += [f"`{'+'.join(base)}` plus:", ""]
        for part in new:
            lines += ["> " + line if line else ">"
                      for line in get_moral_value(part).splitlines()]
            shown.append(part)
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
    parser.add_argument("--allow-mixed", action="store_true",
                        help="build tables even when a group's cells differ "
                             "in an undeclared setting (warn instead of "
                             "refusing).")
    args = parser.parse_args()

    cells = collect_cells(args.paths, strict=not args.allow_mixed)
    builders: List[Callable[[Dict[CellKey, Path]],
                            Union[Table, List[Table], None]]] = [
        state_cooperation_table, probe_b_table, probe_b_token_tables,
        ablation_states_tables,
    ]
    tables: List[Table] = []
    for build in builders:
        built = build(cells)
        if built is not None:
            tables.extend(built if isinstance(built, list) else [built])

    # Default: <group>/analysis/, alongside the cells/ it was computed
    # from. Everything human-readable lives here; cells/ stays machine
    # output and the group root keeps only manifest and batch payloads.
    out_dir = args.out or (args.paths[0] / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    markdown = [readme_header(cells, args.paths),
                moral_values_section()]
    for table in tables:
        tex_path = tex_dir / f"{table.key.replace('-', '_')}.tex"
        tex_path.write_text(to_latex(table))
        markdown.append(to_markdown(table))
        print(f"saved -> {tex_path}")
    md_path = out_dir / f"results_{model_slug(cells)}.md"
    md_path.write_text("\n".join(markdown))
    print(f"saved -> {md_path}\n")
    print("\n".join(markdown[1:]), end="")


if __name__ == "__main__":
    main()
