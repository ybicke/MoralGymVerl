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
        --out eval_results/post_training/qwen_run2_200/analysis/traces.md
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
from publication_tables import Cell, Table, plain, state_label, to_markdown  # noqa: E402
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


def stats_table(acc: Dict, principle_name: str) -> Table:
    rows = []
    for (source, step, state) in sorted(acc):
        a = acc[(source, step, state)]
        p_c = a["c"] / a["legal"] if a["legal"] else float("nan")
        rows.append((f"{source} s{step} {plain(state_label(state))}", [
            Cell(str(a["n"])), Cell(f"{100 * p_c:.0f}"),
            Cell(f"{100 * a['vocab'] / a['n']:.0f}"),
            Cell(f"{100 * a['overlap'] / a['n']:.0f}")]))
    return Table(
        key="trace-stats",
        title="Reasoning-trace statistics",
        caption=(
            "Per source, step and fabricated state, over ALL traces: "
            "cooperation rate of the parsed decisions (\\%), share of "
            "traces using normative language (any of: "
            + ", ".join(NORMATIVE_VOCAB) + "; \\%), and share reproducing "
            f"$\\geq${OVERLAP_WORDS} consecutive words of the "
            f"`{principle_name}` wording (\\%). The principle text is "
            "never in these prompts."),
        stub="Source, step, state",
        col_groups=[(None, ["n"]), (None, ["P(C)"]),
                    (None, ["normative"]), (None, ["principle overlap"])],
        panels=[(None, rows)],
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
