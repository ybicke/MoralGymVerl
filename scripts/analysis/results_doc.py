"""Results-document skeleton shared by every generator.

One document contract for the pre-training screens (2x2 and PGG) and the
post-training checkpoint docs:

    header             generator-specific provenance line
    moral values       verbatim teacher texts            (moral_values_section)
    prompt design      verbatim prompts the model saw    (prompt_design_section)
    tables / figure    generator-specific
    example traces     shortest-of-K per arm x state     (exemplars_section)

plus the Table model and its LaTeX (booktabs) and Markdown renderers.
Stdlib only.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402

from eval_cells import CellData, Decision  # noqa: E402

VALUES = (("none", "None (base)"),
          ("deontological", "Deontological"),
          ("deontological+repair", "Deontological + repair"),
          ("deontological+repair+generosity", "Deontological + repair + generosity"),
          ("utilitarian", "Utilitarian"),
          ("virtue", "Virtue"),
          ("universalization", "Universalization"))
STATES4 = ("CC", "CD", "DC", "DD")
STATES4 = ("CC", "CD", "DC", "DD")
MISSING = "--"
D_OPP = "$\\Delta_{opp}$"


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
    # One-line statement of the measurement surface (what was randomized,
    # which action labels, which protocol). Rendered under the heading in
    # markdown and folded into the caption in LaTeX, so a reader can tell
    # at a glance which regime a table belongs to.
    subtitle: str = ""
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
    # Untitled panels drawn as ruled blocks of ONE grid: LaTeX puts a
    # \midrule between them, markdown (which has no rule syntax) an
    # empty spacer row. For a table whose rows come in short groups.
    ruled_blocks: bool = False


def to_latex(t: Table) -> str:
    ncols = sum(len(cols) for _, cols in t.col_groups)
    caption = f"{t.subtitle} {t.caption}".strip() if t.subtitle else t.caption
    lines = [
        "% Requires \\usepackage{booktabs} in the preamble.",
        "\\begin{table}[t]",
        "\\centering",
        f"\\caption{{{caption}}}",
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
            lines.append("\\midrule" if t.ruled_blocks else "\\addlinespace")
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
    ("$k_O$", "k<sub><small>O</small></sub>"),
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
            else f"**{plain(c.text)}**" if c.bold else plain(c.text))
    # Only "*" needs escaping -- a bare one would open emphasis. Other
    # markers (†, ‡) are literal characters and must not gain a
    # backslash, which markdown would render verbatim.
    return text + (("\\*" if c.marker == "*" else c.marker) if c.marker else "")


def to_markdown(t: Table) -> str:
    if t.pair_groups:
        names = [plain(t.stub)] + [plain(title or cols[0])
                                   for title, cols in t.col_groups]
    else:
        names = [plain(t.stub)] + [plain(f"{title} {c}" if title else c)
                                   for title, cols in t.col_groups
                                   for c in cols]
    names = [n.replace("|", "\\|") for n in names]   # bare | breaks the grid
    out = [f"### {plain(t.title)}", ""]
    if t.subtitle:
        out += [f"*{plain(t.subtitle)}*", ""]
    out += [plain(t.caption), ""]
    for i, (panel, rows) in enumerate(t.panels):
        if panel:
            out += [f"**{plain(panel)}**", ""]
            if t.panel_notes.get(panel):
                out += [plain(t.panel_notes[panel]), ""]
        if t.ruled_blocks and i:
            out.append("|" + " |" * len(names))
        else:
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
            # Row labels are stored in LaTeX form (to_latex emits them
            # verbatim); convert here so the .tex stays free of HTML.
            out.append("| " + " | ".join([plain(label)] + rendered) + " |")
        if not t.ruled_blocks:
            out.append("")
    if t.ruled_blocks:
        out.append("")
    out += [f"*{plain(n)}*" for n in t.notes] + [""]
    return "\n".join(out)


def pct(p: float) -> str:
    return f"{round(100 * p)}"


def moral_values_section() -> str:
    """Verbatim teacher texts, quoted from the single source of truth
    (game/moral_values.py) so the tables read without the codebase.
    A composite quotes only the paragraphs it ADDS to components already
    listed above it, so shared text appears once."""
    lines = [
        "### Moral value prompts",
        "",
        "Teacher texts of the sweep's moral principles, verbatim from "
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




# ------------------------------------------------------------ prompt design

def _split_moral_block(prompt: str) -> Tuple[str, str]:
    """(game prompt, moral block) -- the teacher wrapper appends the block
    after the game prompt; an unwrapped prompt returns ("", prompt)."""
    marker = "\n\nMoral value to follow:"
    i = prompt.find(marker)
    return (prompt[:i], prompt[i:]) if i >= 0 else (prompt, "")


def prompt_design_section(cells: Sequence[CellData]) -> str:
    """The prompts exactly as the model saw them, read from the first
    decision of each cell, so the doc carries its own prompt design and
    cannot drift from the run.

    One block per distinct game prompt (representation x game_description
    x game), taken from the `none` arm where present; then ONE wrapped
    example showing how a moral block attaches, so the teacher template
    is visible without repeating the game text per arm.
    """
    by_design: Dict[Tuple, CellData] = {}
    for c in cells:
        key = (c.meta.get("game_type"), c.meta.get("representation"),
               bool(c.meta.get("game_description")))
        cur = by_design.get(key)
        if cur is None or (cur.arm != "none" and c.arm == "none"):
            by_design[key] = c
    lines = ["### Prompt design", "",
             "Verbatim prompt of the first decision of one cell per game "
             "prompt (`none` arm where available; the history sentence is "
             "that episode's fabricated state). Every other cell differs "
             "only in the fabricated state and, for moral arms, in the block "
             "appended after the game prompt (one example below).", ""]
    for key in sorted(by_design, key=str):
        c = by_design[key]
        game, rep, desc = key
        d = c.decisions[0]
        game_prompt, _ = _split_moral_block(d.prompt)
        lines += [f"**`{game}` · representation `{rep}` · game_description "
                  f"{'on' if desc else 'off'}** (cell `{c.run_dir.name}`, "
                  f"episode {d.episode}, state {d.state})", "",
                  "```", game_prompt.rstrip(), "```", ""]
    wrapped = next((c for c in cells if c.arm != "none"
                    and _split_moral_block(c.decisions[0].prompt)[1]), None)
    if wrapped is not None:
        d = wrapped.decisions[0]
        lines += [f"**Moral block as appended** (arm `{wrapped.arm}`, cell "
                  f"`{wrapped.run_dir.name}`; the game prompt above precedes "
                  "it unchanged)", "", "```",
                  _split_moral_block(d.prompt)[1].strip(), "```", ""]
    return "\n".join(lines + [""])


# ------------------------------------------------------------ exemplars

def _pick(pool: List[Decision], k: int, rng: random.Random) -> Decision:
    """The shortest of k traces drawn at random: readable, content-blind."""
    return min(rng.sample(pool, min(k, len(pool))), key=lambda d: len(d.trace))


def state_caption(state: str) -> str:
    """'CD' -> C_A D_O for 2x2; 'C2' -> own C, k = 2 for PGG."""
    if state == "first":
        return "first round"
    own, obs = state[0], state[1:]
    if obs in ("C", "D"):
        return plain(state_label(state))
    return f"own {own}, k = {obs}"


def exemplars_section(cells: Sequence[CellData], k: int = 8, seed: int = 0,
                      max_chars: int = 2500, per_state: int = 1,
                      tags: Optional[Callable[[CellData, Decision], List[str]]] = None,
                      arm_order: Optional[Sequence[str]] = None) -> str:
    """Verbatim example traces, per arm x fabricated state.

    Selection is content-blind: per (arm, state), `per_state` draws of
    the shortest of `k` traces sampled with a fixed seed. `tags(cell,
    decision)` may add measure classes to the header line (e.g. the
    label-valence verdict), so a reader sees what the tables counted.
    """
    rng = random.Random(seed)
    order = list(arm_order or []) + sorted({c.arm for c in cells}
                                           - set(arm_order or []))
    lines = ["### Example traces", "",
             f"Per arm and fabricated state: the shortest of {k} traces drawn "
             f"with seed {seed} ({per_state} per state); traces longer than "
             f"{max_chars} characters are cut with `[…]`. Header: arm · state · "
             "move" + (" · measure tags" if tags else "") + ".", ""]
    for arm in order:
        for c in [c for c in cells if c.arm == arm]:
            by_state: Dict[str, List[Decision]] = defaultdict(list)
            for d in c.decisions:
                by_state[d.state].append(d)
            for state in sorted(by_state):
                pool = list(by_state[state])
                for _ in range(min(per_state, len(pool))):
                    d = _pick(pool, k, rng)
                    pool.remove(d)
                    head = f"#### `{arm}` · {state_caption(state)} · move {d.move}"
                    if tags:
                        extra = tags(c, d)
                        if extra:
                            head += " · " + " · ".join(extra)
                    body = d.trace.strip()
                    if len(body) > max_chars:
                        body = body[:max_chars] + " […]"
                    lines += [head, "", "```", body, "```", ""]
    return "\n".join(lines + [""])
