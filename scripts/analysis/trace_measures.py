#!/usr/bin/env python3.11
"""Reasoning-trace measures: normative-language and principle-overlap rates.

Library for the post-training analysis scripts (no CLI). Defines, once,
what counts as reasoning in moral terms and what counts as reciting the
trained principle, and the loaders that turn training rollouts or
checkpoint-eval cells into Trace objects:

  normative_hit        trace contains at least one term of NORMATIVE_VOCAB
  principle_overlap    trace reproduces >= OVERLAP_WORDS consecutive words
                       of the principle's wording (verbatim; keyword-free)
  from_rollouts        rollouts/<step>.jsonl of a training run
  from_cells           checkpoint-eval cells (behavioral.responses.jsonl)
  stats / compact_stats_tables / vocab_windows
                       per-(source, step, state) rates and their tables,
                       used by specs/post_training.py

  render_exemplars     per-step rate tables + one verbatim trace per state
                       (shortest of K, fixed seed) + the step's longest
                       recitation; driven by specs/post_training.py for
                       the traces_checkpoints_*/traces_training_* docs
"""
from __future__ import annotations

import json
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from eval_cells import load_cell  # noqa: E402
from measures import (  # noqa: E402
    NORMATIVE_VOCAB, OVERLAP_WORDS, longest_overlap, normative_hit,
    principle_ngrams, principle_overlap, words as _words,
)
from results_doc import (  # noqa: E402
    MISSING, Cell, Table, plain, state_label, to_markdown,
)
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402
from moralgym_verl.game.prompts import find_action_marker  # noqa: E402

STATES = ("CC", "CD", "DC", "DD")

# What counts as reasoning in moral terms. Word stems, matched
# case-insensitively as whole-word prefixes ("exploit" hits exploited /
# exploitative; "reciproc" hits reciprocity / reciprocate). Reviewed
# 2026-08-24; change here and the rate's definition changes everywhere.
# ------------------------------------------------------------------ sources

class Trace:
    __slots__ = ("source", "step", "state", "text", "move")

    def __init__(self, source, step, state, text, move):
        self.source, self.step, self.state = source, step, state
        self.text, self.move = text, move


def _move(text: str, coop: str, defect: str) -> str:
    m = find_action_marker(text)
    if not m:
        return "illegal"
    tok = m.group(1).strip().rstrip(".,!?;:").upper()
    return ("C" if tok == coop.upper() else
            "D" if tok == defect.upper() else "illegal")


def from_rollouts(run_dir: Path, steps: Iterable[int]) -> List[Trace]:
    out = []
    for step in steps:
        for line in open(run_dir / "rollouts" / f"{step}.jsonl"):
            row = json.loads(line)
            g = json.loads(row["gts"])
            out.append(Trace(f"{run_dir.name} (train)", step, g["fab_agent"] + g["fab_opp"],
                             row["output"],
                             _move(row["output"], g["coop_label"],
                                   g["defect_label"])))
    return out


def from_cells(group: Path) -> List[Trace]:
    """Checkpoint-eval cells via eval_cells.load_cell: step from
    metadata.checkpoint; state, move and trace from the loader."""
    out = []
    for cell_dir in sorted((group / "cells").glob("*")):
        cell = load_cell(cell_dir)
        if cell is None:
            continue
        ck = cell.meta.get("checkpoint", "base")
        m = re.search(r"/([^/]+)/global_step_(\d+)/", ck)
        source = f"{m.group(1)} (eval)" if m else "base (eval)"
        step = int(m.group(2)) if m else 0
        for d in cell.decisions:
            out.append(Trace(source, step, d.state, d.trace, d.move))
    return out


# -------------------------------------------------------------------- stats

def vocab_windows(run_dir: Path, window: int, grams: set) -> Dict[int, Dict]:
    """Per W-step window over ALL training rollouts: normative-language
    and verbatim-recitation counts.

    Dense counterpart to stats(): every step, every rollout, so the
    ONSET of the vocabulary is visible against the behaviour curve in
    the same row of the trajectory table. Streams the files -- the
    traces are never all held in memory at once.
    """
    acc: Dict[int, Dict] = {}
    for f in sorted((run_dir / "rollouts").glob("*.jsonl"),
                    key=lambda q: int(q.stem)):
        a = acc.setdefault((int(f.stem) - 1) // window,
                           {"n": 0, "vocab": 0, "overlap": 0})
        for line in open(f):
            text = json.loads(line)["output"]
            a["n"] += 1
            a["vocab"] += normative_hit(text)
            a["overlap"] += principle_overlap(text, grams)
    return acc


def stats(traces: List[Trace], grams: set) -> Dict[Tuple[str, int, str], Dict]:
    acc: Dict[Tuple[str, int, str], Dict] = defaultdict(
        lambda: {"n": 0, "vocab": 0, "overlap": 0, "c": 0, "legal": 0})
    for t in traces:
        a = acc[(t.source, t.step, t.state)]
        a["n"] += 1
        a["vocab"] += normative_hit(t.text)
        a["overlap"] += principle_overlap(t.text, grams)
        if t.move != "illegal":
            a["legal"] += 1
            a["c"] += t.move == "C"
    return acc


def _stats_caption(principle_name: str, compact: bool) -> str:
    """Column definitions, shared by both table shapes."""
    parts = ["Per training step and fabricated previous state, over ALL "
             "traces at that step. The principle text is never in these "
             "prompts."]
    if compact:
        parts[0] += (" Both blocks are percentages of traces; columns "
                     "are the evaluated checkpoints (s60 = step 60) and "
                     "rows the four states, so a row reads as that "
                     "state's time course and a column as the "
                     "across-state gradient at that step.")
    else:
        parts += [
            "n = every trace generated at that step; the panel heading "
            "gives the total, the n column its split across the four "
            "states. Per step, NOT cumulative across steps.",
            "P(C) = cooperation rate of the parsed decisions, illegal "
            "moves excluded from the denominator."]
    parts += [
        "normative \\% = share of traces containing at least one of "
        + str(len(NORMATIVE_VOCAB)) + " reviewed word stems ("
        + ", ".join(NORMATIVE_VOCAB) + ") anywhere in the trace, matched "
        "on word boundaries (`fair` excludes `fairly`); one hit marks "
        "the whole trace. It measures whether the trace reaches for "
        "moral VOCABULARY at all, NOT whether the norm is applied "
        "correctly -- a trace arguing AGAINST the norm still counts. "
        "Base rate in untrained payoff talk is roughly 2-6\\%, mostly "
        "`exploit`.",
        f"recites principle \\% = the `{principle_name}` wording is split "
        f"into words and all of its {OVERLAP_WORDS}-word n-grams "
        "collected; a trace counts if it reproduces ANY "
        f"{OVERLAP_WORDS} consecutive words of it. VERBATIM only, so a "
        "faithful paraphrase scores 0 -- the two columns separate "
        "quoting from paraphrasing, not understanding from not."]
    return "\n\n".join(parts)


def compact_stats_tables(acc: Dict, principle_name: str) -> List[Table]:
    """Two small tables, one per metric, in Table P1's own orientation:
    steps down, states across.

    Reading them beside the behaviour table is the point -- a step's
    vocabulary row lines up column-for-column with the same step's
    cooperation row. The metrics get a table each because they behave
    differently (one saturates early, the other is flat then jumps), and
    because each caption then defines only its own measure.
    """
    steps = sorted({st for _, st, _ in acc})
    src = next(iter({sc for sc, _, _ in acc}))
    lead = ("Share of reasoning traces (\\%) by training step and "
            "fabricated previous state, over ALL traces at that step; "
            "same episodes as Table P1, so a row here lines up with the "
            "same step's row there. The principle text is never in "
            "these prompts.")
    specs = [
        ("normative", "vocab",
         "Reasoning traces — normative language",
         "A trace counts if it contains at least one of "
         + str(len(NORMATIVE_VOCAB)) + " reviewed word stems ("
         + ", ".join(NORMATIVE_VOCAB) + ") anywhere in it, matched on "
         "word boundaries (`fair` excludes `fairly`); one hit marks the "
         "whole trace. This measures whether the trace reaches for moral "
         "VOCABULARY at all, NOT whether the norm is applied correctly "
         "-- a trace arguing AGAINST the norm still counts. Base rate in "
         "untrained payoff talk is roughly 2-6\\%, mostly `exploit`."),
        ("recites", "overlap",
         "Reasoning traces — verbatim recitation of the principle",
         f"The `{principle_name}` wording is split into words and all of "
         f"its {OVERLAP_WORDS}-word n-grams collected; a trace counts if "
         f"it reproduces ANY {OVERLAP_WORDS} consecutive words of it. "
         "VERBATIM only, so a faithful paraphrase scores 0: against the "
         "table above, the difference between the two is quoting vs. "
         "paraphrasing, not understanding vs. not."),
    ]
    tables = []
    for key, metric, title, definition in specs:
        rows = []
        for st in steps:
            cells = []
            for state in STATES:
                a = acc.get((src, st, state))
                cells.append(Cell(f"{100 * a[metric] / a['n']:.0f}")
                             if a and a["n"] else Cell(MISSING))
            rows.append((f"step {st}", cells))
        tables.append(Table(
            key=f"trace-stats-{key}",
            title=title,
            caption=f"{lead}\n\n{definition}",
            stub="Checkpoint",
            col_groups=[(None, [state_label(x)]) for x in STATES],
            panels=[(None, rows)],
        ))
    return tables

# ---------------------------------------------------------------- exemplars

SUB = {"CC": "C<sub>A</sub>C<sub>O</sub>", "CD": "C<sub>A</sub>D<sub>O</sub>",
       "DC": "D<sub>A</sub>C<sub>O</sub>", "DD": "D<sub>A</sub>D<sub>O</sub>"}


def parse_steps(spec: str) -> List[int]:
    """'20,40,...,200' expands the arithmetic progression; plain lists pass."""
    parts = [p.strip() for p in spec.split(",")]
    if "..." in parts:
        i = parts.index("...")
        a, b, end = int(parts[i - 2]), int(parts[i - 1]), int(parts[i + 1])
        return [int(p) for p in parts[:i - 2]] + list(range(a, end + 1, b - a))
    return [int(p) for p in parts]


def pick(pool: List[Trace], k: int, rng: random.Random) -> Trace:
    return min(rng.sample(pool, min(k, len(pool))), key=lambda t: len(t.text))


def render_exemplars(title: str, surface: str, steps: List[int], traces: List[Trace],
           principle_name: str, k: int, seed: int, max_chars: int) -> str:
    grams = principle_ngrams(get_moral_value(principle_name))
    pw = _words(get_moral_value(principle_name))
    rng = random.Random(seed)
    by: Dict[Tuple[int, str], List[Trace]] = defaultdict(list)
    for t in traces:
        by[(t.step, t.state)].append(t)

    md = [f"# {title}", "", surface, "",
          f"*Normative* = trace contains ≥1 of {len(NORMATIVE_VOCAB)} reviewed stems "
          f"({', '.join(NORMATIVE_VOCAB)}). *Recites* = reproduces ≥{OVERLAP_WORDS} "
          f"consecutive words of the '{principle_name}' wording verbatim. "
          f"*Overlap* = longest verbatim word run shared with that wording "
          f"(median / max over the state's traces). The wording is never in "
          f"these prompts (SDPO: teacher context only; GRPO: never shown).", "",
          f"Exemplar selection: per (step, state), the shortest of {k} traces "
          f"sampled with seed {seed}; traces longer than {max_chars} chars are "
          f"cut with `[…]`. **Bold** in a header = the recited span. Each step "
          f"ends with its single longest recitation, whichever state.", ""]

    for step in steps:
        md += [f"## Step {step}", "",
               "| State | n | P(C) % | normative % | recites % | overlap median / max |",
               "|---|---|---|---|---|---|"]
        for st in STATES:
            pool = by.get((step, st), [])
            if not pool:
                md.append(f"| {SUB[st]} | 0 | — | — | — | — |")
                continue
            legal = [t for t in pool if t.move != "illegal"]
            pc = 100 * sum(t.move == "C" for t in legal) / max(1, len(legal))
            nrm = 100 * sum(normative_hit(t.text) for t in pool) / len(pool)
            rec = 100 * sum(principle_overlap(t.text, grams) for t in pool) / len(pool)
            ov = [longest_overlap(t.text, pw)[0] for t in pool]
            md.append(f"| {SUB[st]} | {len(pool)} | {pc:.0f} | {nrm:.0f} | {rec:.0f} "
                      f"| {statistics.median(ov):.0f} / {max(ov)} |")
        md.append("")
        for st in STATES:
            pool = by.get((step, st), [])
            if not pool:
                continue
            t = pick(pool, k, rng)
            L, i = longest_overlap(t.text, pw)
            span = " ".join(_words(t.text)[i:i + L]) if L >= OVERLAP_WORDS else ""
            flags = ("normative" if normative_hit(t.text) else "payoff-only")
            head = f"### step {step} · {SUB[st]} · move {t.move} · {flags}"
            if span:
                head += f" · recites {L} words: **{span}**"
            body = t.text.strip()
            if len(body) > max_chars:
                body = body[:max_chars] + " […]"
            md += [head, "", "```", body, "```", ""]
        allstep = [t for st in STATES for t in by.get((step, st), [])]
        if allstep:
            t = max(allstep, key=lambda t: longest_overlap(t.text, pw)[0])
            L, i = longest_overlap(t.text, pw)
            if L >= OVERLAP_WORDS:
                span = " ".join(_words(t.text)[i:i + L])
                body = t.text.strip()
                if len(body) > max_chars:
                    body = body[:max_chars] + " […]"
                md += [f"### step {step} · longest recitation · {SUB[t.state]} · move {t.move} "
                       f"· {L} words: **{span}**", "", "```", body, "```", ""]
            else:
                md += [f"*step {step}: no trace reaches {OVERLAP_WORDS} words of overlap "
                       f"(max {L}).*", ""]
    return "\n".join(md)
