#!/usr/bin/env python3.11
"""Reasoning traces as evidence: reproducible samples plus two rates.

Reading a handful of traces is an anecdote. This makes the qualitative
comparison citable: for each (source, step, state) it computes, over ALL
traces,

  normative-language rate  share of traces containing at least one term
                           from NORMATIVE_VOCAB (reasons in moral terms
                           at all -- a judgment call, hence the list is
                           one constant, reviewed, not buried)
  principle-overlap rate   share of traces reproducing >= OVERLAP_WORDS
                           consecutive words of the trained principle's
                           wording (verbatim recitation; keyword-free)

and writes a fixed-seed sample of traces verbatim, so nothing is
cherry-picked.

Sources (mix freely):
  --rollouts RUN_DIR --steps 60,120,200   training dumps (rollouts/<step>.jsonl)
  --cells GROUP_DIR                        checkpoint-eval cells
                                           (behavioral.responses.jsonl;
                                           state from the balanced cycle)

Login node, stdlib only (the principle text comes from moral_values.py):
    /usr/bin/python3.11 scripts/analysis/trace_comparison.py \
        --rollouts ~/logs_verl/runs/qwen_run2_200 --steps 60,120,200 \
        --rollouts ~/logs_verl/runs/grpo_deon_tft_200 --steps 60,120,180 \
        --principle deontological+repair+generosity \
        --out eval_results/post_training/qwen3-8b-pd-sdpo-deon-repair-gen/analysis/results_traces_qwen3-8b-pd-sdpo-deon-repair-gen.md
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from publication_tables import (  # noqa: E402
    MISSING, Cell, Table, plain, state_label, to_markdown,
)
from moralgym_verl.game.moral_values import get_moral_value  # noqa: E402
from moralgym_verl.game.prompts import find_action_marker  # noqa: E402

STATES = ("CC", "CD", "DC", "DD")
FAB_CYCLE = ("CC", "CD", "DC", "DD")       # eval episode i -> state i % 4

# What counts as reasoning in moral terms. Word stems, matched
# case-insensitively as whole-word prefixes ("exploit" hits exploited /
# exploitative; "reciproc" hits reciprocity / reciprocate). Reviewed
# 2026-08-24; change here and the rate's definition changes everywhere.
NORMATIVE_VOCAB = (
    "good faith", "trust", "exploit", "moral", "ethic", "principle",
    "fair", "reciproc", "wrong", "obligat", "betray", "honest",
)
OVERLAP_WORDS = 6

_WORD = re.compile(r"[a-z']+")


def _words(text: str) -> List[str]:
    return _WORD.findall(text.lower())


def normative_hit(text: str) -> bool:
    t = text.lower()
    # "fair" must not fire on "fairly (likely)"; the other stems are safe.
    return any(re.search(r"\b" + re.escape(v) + (r"(?!ly)" if v == "fair" else ""), t)
               for v in NORMATIVE_VOCAB)


def principle_ngrams(principle: str, n: int = OVERLAP_WORDS) -> set:
    w = _words(principle)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def principle_overlap(text: str, grams: set, n: int = OVERLAP_WORDS) -> bool:
    w = _words(text)
    return any(tuple(w[i:i + n]) in grams for i in range(len(w) - n + 1))


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
    """Checkpoint-eval cells: step from metadata.checkpoint, state from
    the balanced episode cycle, decision from the eval's own parse
    (behavioral.json episode_moves, index-aligned with the responses)."""
    out = []
    for cell in sorted((group / "cells").glob("*")):
        resp = cell / "behavioral.responses.jsonl"
        if not resp.exists():
            continue
        beh = json.load(open(cell / "behavioral.json"))
        ck = beh["metadata"].get("checkpoint", "base")
        m = re.search(r"/([^/]+)/global_step_(\d+)/", ck)
        source = f"{m.group(1)} (eval)" if m else "base (eval)"
        step = int(m.group(2)) if m else 0
        opponents = beh["opponents"]
        if len(opponents) != 1:
            raise SystemExit(f"{cell.name}: expected one opponent block")
        moves = [ep["agent"][0] for ep in opponents[0]["episode_moves"]]
        for i, line in enumerate(open(resp)):
            text = json.loads(line)["raw"]
            mv = moves[i] if moves[i] in ("C", "D") else "illegal"
            out.append(Trace(source, step, FAB_CYCLE[i % 4], text, mv))
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


def stats_table(acc: Dict, principle_name: str,
                compact: bool = False) -> Table:
    """One panel per (source, step); the source is named once in the
    panel heading instead of being repeated on every state row.

    compact=True drops the n and P(C) columns, for a document that
    already states the episode count once (n per state is then fixed)
    and already carries the cooperation rates in its own behaviour
    table -- repeating either here is duplication. Standalone callers
    keep them: they may mix sources whose n genuinely differs.
    """
    panels: List = []
    by_panel: Dict[Tuple[str, int], List] = defaultdict(list)
    for (source, step, state) in sorted(acc, key=lambda k: (k[0], k[1], k[2])):
        by_panel[(source, step)].append((state, acc[(source, step, state)]))

    def panel_order(item):
        (source, step), _ = item
        return (0 if source.endswith("(train)") else 1, source, step)

    one_source = len({src for src, _ in by_panel}) == 1

    for (source, step), entries in sorted(by_panel.items(), key=panel_order):
        rows, total = [], sum(a["n"] for _, a in entries)
        for state, a in entries:
            cells = []
            if not compact:
                p_c = a["c"] / a["legal"] if a["legal"] else float("nan")
                cells += [Cell(str(a["n"])), Cell(f"{100 * p_c:.0f}")]
            cells += [Cell(f"{100 * a['vocab'] / a['n']:.0f}"),
                      Cell(f"{100 * a['overlap'] / a['n']:.0f}")]
            rows.append((state_label(state), cells))
        title = f"Step {step}" if one_source else f"{source}, step {step}"
        panels.append((title if compact else f"{title} (n = {total})", rows))

    caption = _stats_caption(principle_name, compact)
    return Table(
        key="trace-stats",
        title="Reasoning-trace statistics",
        caption=caption,
        stub="State",
        col_groups=([] if compact
                    else [(None, ["n"]), (None, ["P(C)"])])
        + [(None, ["normative \\%"]), (None, ["recites principle \\%"])],
        panels=panels,
    )


# ------------------------------------------------------------------- sample

def sample(traces: List[Trace], per_cell: int, seed: int) -> List[Trace]:
    rng = random.Random(seed)
    by_cell: Dict[Tuple[str, int, str], List[Trace]] = defaultdict(list)
    for t in traces:
        by_cell[(t.source, t.step, t.state)].append(t)
    out = []
    for key in sorted(by_cell):
        pool = by_cell[key]
        out += rng.sample(pool, min(per_cell, len(pool)))
    return out


def render_traces(chosen: List[Trace], grams: set, max_chars: int) -> str:
    md = ["## Sampled traces", "",
          "Fixed-seed sample, verbatim; header = source, step, fabricated "
          "state, parsed decision, normative-language / principle-overlap "
          "flags.", ""]
    for t in chosen:
        flags = (("normative " if normative_hit(t.text) else "")
                 + ("principle-overlap" if principle_overlap(t.text, grams) else "")
                 ).strip() or "—"
        md += [f"### {t.source} · step {t.step} · {t.state} · move {t.move} · {flags}",
               "", "```", t.text.strip()[:max_chars]
               + (" […]" if len(t.text.strip()) > max_chars else ""),
               "```", ""]
    return "\n".join(md)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rollouts", action="append", type=Path, default=[],
                        help="run dir with rollouts/ (repeatable; pair each "
                             "with a --steps)")
    parser.add_argument("--steps", action="append", default=[],
                        help="comma-separated steps for the matching --rollouts")
    parser.add_argument("--cells", action="append", type=Path, default=[],
                        help="checkpoint-eval group dir (repeatable)")
    parser.add_argument("--principle", default="deontological+repair+generosity",
                        help="moral value whose wording defines the overlap rate")
    parser.add_argument("--per-cell", type=int, default=2,
                        help="sampled traces per (source, step, state)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--states", default="CC,CD,DC,DD")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if len(args.rollouts) != len(args.steps):
        raise SystemExit("give one --steps per --rollouts")

    keep = set(args.states.split(","))
    traces: List[Trace] = []
    for run, steps in zip(args.rollouts, args.steps):
        traces += from_rollouts(run, [int(s) for s in steps.split(",")])
    for group in args.cells:
        traces += from_cells(group)
    traces = [t for t in traces if t.state in keep]
    if not traces:
        raise SystemExit("no traces loaded")

    grams = principle_ngrams(get_moral_value(args.principle))
    acc = stats(traces, grams)
    md = ["# Reasoning traces", "",
          to_markdown(stats_table(acc, args.principle)),
          render_traces(sample(traces, args.per_cell, args.seed), grams,
                        args.max_chars)]
    text = "\n".join(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"saved -> {args.out}")
    else:
        print(text[:3000])


if __name__ == "__main__":
    main()
