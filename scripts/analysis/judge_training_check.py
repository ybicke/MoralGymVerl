"""Training-side check of a judge-GRPO run, from its saved rollouts.

Design: ~/moralgym-report/notes/judge_grpo_design.tex (checks 2 and 4) and
~/moralgym-report/notes/experiment_roadmap.tex (experiment 1). Answers, per
window of training steps, before any transfer evaluation:

  policy   P(C) in each fabricated state (your last move, the other player's
           last move), the quantity of the paper's training figure
  judge    mean judge score by the other player's last move and the action:
           reciprocate (C after C), exploit (D after C), forgive (C after D),
           retaliate (D after D). Exploit should score low; forgive against
           retaliate shows which policy the judge's reading of the principle
           favours
  signal   share of 16-rollout groups with a flat reward (std < FLAT_STD) and
           with one action only: flat groups give GRPO no advantage
  hacking  words per response, share with normative vocabulary, share reciting
           6 consecutive words of the principle, and the rank correlation of
           the judge score with length and with recitation among cooperative
           responses (so the action does not drive it)
  reasons  which way the exploitation language points: SELF (the agent is or
           could be exploited, a self-protective, strategic reason) against
           OTHER (the agent would exploit the other player, or must not: the
           principle's direction); share using "good faith", the principle's
           own test; and MISREAD, the share claiming the agent is being
           exploited in the state where it defected on a cooperator, which
           misreads the history. Regex measures, a lower bound for paraphrase.

Input: <run dir>/rollouts/<step>.jsonl as written during training (one record
per rollout: input, output, gts, score, judge_score, the action flags, and
`feedback`, which holds the principle text). Torch-free.

Usage:
    python scripts/analysis/judge_training_check.py \\
        $SCRATCH/moralgym_verl_runs/qwen3_8b_grpo_pd_judge-think_tft_200_seed1/rollouts
    python scripts/analysis/judge_training_check.py <rollouts dir> --window 20 --json out.json
    python scripts/analysis/judge_training_check.py <rollouts dir> --window 20 \\
        --md eval_results/post_training/<run>/classic/training_rollouts/analysis/results_training_rollouts.md
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measures import normative_hit, principle_ngrams, principle_overlap, words  # noqa: E402

FLAT_STD = 0.02  # as moralgym_verl.training.judge.FLAT_STD
STATES = [("C", "C"), ("C", "D"), ("D", "C"), ("D", "D")]  # (own, other) last move
CELLS = [("reciprocate", "C", "C"), ("exploit", "C", "D"),
         ("forgive", "D", "C"), ("retaliate", "D", "D")]  # (name, other's last move, action)

# Direction of exploitation language (the opponent is "A"/"agent A" in the PD prompt).
SELF = re.compile(
    r"(?:being|been|get|getting|be|avoid|risk of|prevent)\s+(?:\w+\s+){0,2}?"
    r"(?:exploited|taken advantage of|betrayed)"
    r"|exploited by|exploit(?:s|ing)? (?:you|me|us)\b|take advantage of (?:you|me)\b"
    r"|(?:a|they|agent a) (?:\w+\s+){0,2}?(?:is|are|was|were|may be|might be|could be)? ?"
    r"exploiting (?:you|your)|protect (?:yourself|myself)", re.I)
OTHER = re.compile(
    r"exploit(?:s|ing|ation of)? (?:agent a|a\b|them|the other|their|other players|others|"
    r"the group|cooperat)"
    r"|tak(?:e|ing) advantage of (?:agent a|a\b|them|the other|their|others)"
    r"|(?:not|never|wrong to|avoid|refrain from|instead of) (?:\w+\s+){0,2}?"
    r"(?:exploit|take advantage|taking advantage|betray)", re.I)
GOOD_FAITH = re.compile(r"good faith", re.I)


def load_step(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            state = json.loads(r["gts"])
            if r["parse_fail_rate"] == 1.0:
                action = None
            else:
                action = "C" if r["cooperation_rate"] == 1.0 else "D"
            rows.append({
                "group": r["input"],
                "own": state["fab_agent"], "other": state["fab_opp"],
                "action": action,
                "score": float(r["score"]),
                "judge": float(r["judge_score"]),
                "judge_invalid": float(r.get("judge_invalid_rate", 0.0)),
                "text": r["output"],
                "principle": r.get("feedback", ""),
            })
    return rows


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation with average ranks for ties; NaN if undefined."""
    if len(xs) < 3:
        return float("nan")
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = mean(rx), mean(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sxx * syy) ** 0.5


def summarize(rows: list[dict]) -> dict:
    out: dict = {"n": len(rows)}
    legal = [r for r in rows if r["action"] is not None]
    out["parse_fail"] = 1 - len(legal) / len(rows)
    out["judge_invalid"] = mean([r["judge_invalid"] for r in rows])

    for own, other in STATES:
        xs = [r["action"] == "C" for r in legal if r["own"] == own and r["other"] == other]
        out[f"pC_{own}{other}"] = mean([float(x) for x in xs])

    for name, other, action in CELLS:
        out[f"judge_{name}"] = mean([r["judge"] for r in legal
                                     if r["other"] == other and r["action"] == action])

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    flat = [statistics.pstdev([r["score"] for r in g]) < FLAT_STD for g in groups.values()]
    one_action = [len({r["action"] for r in g}) == 1 for g in groups.values()]
    out["groups"] = len(groups)
    out["flat_groups"] = mean([float(x) for x in flat])
    out["one_action_groups"] = mean([float(x) for x in one_action])

    grams = principle_ngrams(rows[0]["principle"]) if rows[0]["principle"] else set()
    lengths = [len(words(r["text"])) for r in rows]
    out["words"] = mean([float(x) for x in lengths])
    out["normative"] = mean([float(normative_hit(r["text"])) for r in rows])
    out["recites"] = mean([float(principle_overlap(r["text"], grams)) for r in rows]) if grams else float("nan")

    out["self"] = mean([float(bool(SELF.search(r["text"]))) for r in rows])
    out["other"] = mean([float(bool(OTHER.search(r["text"]))) for r in rows])
    out["good_faith"] = mean([float(bool(GOOD_FAITH.search(r["text"]))) for r in rows])
    out["misread"] = mean([float(bool(SELF.search(r["text"]))) for r in rows
                           if r["own"] == "D" and r["other"] == "C"])

    coop = [r for r in legal if r["action"] == "C"]
    out["rho_length"] = spearman([float(len(words(r["text"]))) for r in coop],
                                 [r["judge"] for r in coop])
    out["rho_normative"] = spearman([float(normative_hit(r["text"])) for r in coop],
                                    [r["judge"] for r in coop])
    return out


COLUMNS = [
    ("pC_CC", "CC", 100), ("pC_CD", "CD", 100), ("pC_DC", "DC", 100), ("pC_DD", "DD", 100),
    ("judge_reciprocate", "recip", 1), ("judge_exploit", "expl", 1),
    ("judge_forgive", "forg", 1), ("judge_retaliate", "retal", 1),
    ("flat_groups", "flat%", 100), ("one_action_groups", "1act%", 100),
    ("words", "words", 1), ("normative", "norm%", 100), ("recites", "recit%", 100),
    ("rho_length", "rho_len", 1), ("rho_normative", "rho_nrm", 1),
    ("self", "self%", 100), ("other", "other%", 100), ("good_faith", "gfaith%", 100),
    ("misread", "misrd%", 100),
    ("parse_fail", "fail%", 100), ("judge_invalid", "jinv%", 100),
]

MD_TABLES = [
    ("Table J1 — policy: P(C) (%) by fabricated previous round (own, other)",
     ["pC_CC", "pC_CD", "pC_DC", "pC_DD"]),
    ("Table J2 — judge: mean score by case (other's last move, action)",
     ["judge_reciprocate", "judge_exploit", "judge_forgive", "judge_retaliate"]),
    ("Table J3 — signal and failures (% of groups or rollouts)",
     ["flat_groups", "one_action_groups", "parse_fail", "judge_invalid"]),
    ("Table J4 — trace language (% of rollouts; words = mean words per response)",
     ["words", "normative", "recites", "rho_length", "rho_normative"]),
    ("Table J5 — direction of the exploitation language (% of rollouts)",
     ["self", "other", "good_faith", "misread"]),
]

MD_LEGEND = """\
**Measures.** States are (the agent's, the other player's) move in the
fabricated previous round; the opponent is tit-for-tat. Judge cases:
reciprocate = C after the other's C, exploit = D after C, forgive = C after
D, retaliate = D after D. flat = 16-rollout groups with reward std < 0.02 (no
GRPO advantage); 1act = groups with a single action. normative = at least one
of the 12 reviewed stems (measures.NORMATIVE_VOCAB); recit = 6 consecutive
words of the principle verbatim; rho = Spearman correlation of the judge score
with length or with normative vocabulary among cooperative rollouts.
self = the trace says the agent is or could be exploited (a self-protective,
strategic reason); other = it says the agent would exploit the other player,
or must not (the principle's direction); gfaith = uses "good faith", the
principle's own test; misrd = self among rollouts in state (D, C), where the
agent itself defected on a cooperator, i.e. a misread history. Regex measures:
they miss paraphrase and can misfire on negations, so read them as
approximate shares, not labels."""


def fmt(key: str, value: float, scale: int) -> str:
    if value != value:  # NaN
        return "    --"
    if scale == 100 or key == "words":
        return f"{value * scale:6.0f}"
    return f"{value:6.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rollouts", type=Path, help="<run dir>/rollouts with <step>.jsonl files")
    ap.add_argument("--window", type=int, default=10, help="steps pooled per row (default 10)")
    ap.add_argument("--json", type=Path, help="also write the rows to this file")
    ap.add_argument("--md", type=Path, help="also write a results document (Markdown)")
    args = ap.parse_args()

    steps = sorted(int(p.stem) for p in args.rollouts.glob("*.jsonl") if p.stem.isdigit())
    if not steps:
        sys.exit(f"no <step>.jsonl files in {args.rollouts}")

    bins: dict[int, list[int]] = defaultdict(list)
    for s in steps:
        bins[(s - 1) // args.window].append(s)

    print(f"{args.rollouts}: {len(steps)} steps ({steps[0]}..{steps[-1]}), window {args.window}")
    print("P(C) % by state (own, other last move) | mean judge score by case | signal | text | bias | invalid")
    print("  steps    n " + " ".join(f"{label:>6}" for _, label, _ in COLUMNS))
    result = []
    for b in sorted(bins):
        rows = [r for s in bins[b] for r in load_step(args.rollouts / f"{s}.jsonl")]
        summary = summarize(rows)
        label = f"{bins[b][0]}-{bins[b][-1]}"
        print(f"{label:>7} {summary['n']:4d} "
              + " ".join(fmt(k, summary[k], scale) for k, _, scale in COLUMNS))
        result.append({"steps": bins[b], **summary})
    if args.json:
        args.json.write_text(json.dumps(result, indent=1))
        print(f"wrote {args.json}")
    if args.md:
        write_md(args.md, args.rollouts, steps, args.window, result)
        print(f"wrote {args.md}")


def write_md(path: Path, rollouts: Path, steps: list[int], window: int, result: list[dict]) -> None:
    labels = {k: label for k, label, _ in COLUMNS}
    scales = {k: scale for k, _, scale in COLUMNS}
    run = rollouts.resolve().parent.name
    lines = [
        f"# Training rollouts: {run}",
        "",
        f"Generated by `scripts/analysis/judge_training_check.py` from `{rollouts}` "
        f"({len(steps)} steps, {steps[0]}..{steps[-1]}, pooled in windows of {window} steps; "
        f"{result[0]['n']} rollouts per full window). Training game: single-round PD "
        "against tit-for-tat with a fabricated previous round; the reward is the "
        "judge's score, the game payoff is only logged. Hand-written reading goes in "
        "analysis_*.md beside this file.",
        "",
    ]
    for title, keys in MD_TABLES:
        lines += [f"### {title}", "",
                  "| steps | " + " | ".join(labels[k] for k in keys) + " |",
                  "|---|" + "---|" * len(keys)]
        for row in result:
            cells = [fmt(k, row[k], scales[k]).strip() for k in keys]
            lines.append(f"| {row['steps'][0]}-{row['steps'][-1]} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += [MD_LEGEND, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
