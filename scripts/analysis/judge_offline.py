"""Offline comparison of the judge variants on saved evaluation traces.

Design: ~/moralgym-report/notes/judge_grpo_design.tex (check 1, "Choosing
between them"). Scores the same traces with each judge variant against a
running vLLM judge server and reports, per variant:

  (1) spread     within 16-trace groups (same policy and prompt), as in GRPO:
                 mean within-group std, share of flat groups (std < 0.02)
  (2) validity   AUC of cooperating vs defecting after the other player
                 cooperated (exploitation should score low); mean score per
                 policy family (SDPO should score above GRPO)
  (3) bias       Spearman correlation of the score with response length and
                 with overlap with the principle's wording, among cooperative
                 responses only (so the action does not drive it)
  plus the invalid rate and seconds per 256 calls.

Rule (decided 2026-11-19): keep the variant with more spread, better
validity and less bias; thinking must clearly win on validity to justify its
cost; without a clear winner, yesno.

Input: an MRI-dashboard data file (moralgym_mri_dashboard/site/data/
<model>__pd_single_round.json): `texts` (game prompts) and `policies`
({cell: [{p: prompt index, m: action C/D, r: response, c: history}]}),
history = (your last move, the other player's last move). Only the traces
are used, never the Jev judgments stored with them.

Usage (judge server already running, e.g. scripts/slurm/alliance/judge_offline.sh):
    python scripts/analysis/judge_offline.py --data qwen3_8b__pd_single_round.json \\
        --endpoint http://localhost:8000/v1 --out judge_offline_out
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
from collections import defaultdict
from pathlib import Path

from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.training.judge import JudgeConfig, judge_prompt_from_text, score_prompts

VARIANTS = {
    "yesno": {"scoring": "yesno", "thinking": False},
    "scale": {"scoring": "scale", "thinking": False},
    "yesno_think": {"scoring": "yesno", "thinking": True},
}
FLAT_STD = 0.02


# ── Statistics (standard library only) ───────────────────────────────────────

def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _ranks(xs):
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


def spearman(xs, ys):
    if len(xs) < 3:
        return float("nan")
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = _mean(rx), _mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def auc(pos, neg):
    """P(a positive scores above a negative), ties count half."""
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def principle_overlap(response: str, principle: str, n: int = 3) -> float:
    """Share of the principle's word n-grams that appear in the response."""
    words = lambda t: re.findall(r"[a-z']+", t.lower())
    grams = lambda w: {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}
    p = grams(words(principle))
    return len(p & grams(words(response))) / len(p) if p else 0.0


def family(cell: str) -> str:
    for f in ("base_moral_ctx", "grpo", "sdpo", "base"):
        if cell.startswith(f):
            return f
    return cell


# ── Main ─────────────────────────────────────────────────────────────────────

def load_traces(path: Path, per_prompt: int, policies: list[str] | None, seed: int):
    data = json.loads(path.read_text())
    rng = random.Random(seed)
    traces = []
    for cell, items in data["policies"].items():
        if policies and cell not in policies:
            continue
        by_prompt = defaultdict(list)
        for x in items:
            if x.get("m") in ("C", "D"):
                by_prompt[x["p"]].append(x)
        for p, xs in sorted(by_prompt.items()):
            for x in rng.sample(xs, min(per_prompt, len(xs))):
                traces.append({"cell": cell, "family": family(cell), "p": p,
                               "history": x["c"], "action": x["m"],
                               "response": x["r"], "game": data["texts"][p]})
    return traces


def summarize(name, rows, seconds):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["cell"], r["p"])].append(r["score"])
    stds = [_std(v) for v in groups.values()]
    after_c = [r for r in rows if r["history"][1] == "C"]
    coop = [r for r in rows if r["action"] == "C"]
    fam = defaultdict(list)
    for r in rows:
        fam[r["family"]].append(r["score"])
    return {
        "variant": name,
        "n": len(rows),
        "invalid_rate": _mean([r["invalid"] for r in rows]),
        "group_std_mean": _mean(stds),
        "flat_group_share": _mean([s < FLAT_STD for s in stds]),
        "auc_coop_vs_exploit": auc([r["score"] for r in after_c if r["action"] == "C"],
                                   [r["score"] for r in after_c if r["action"] == "D"]),
        "mean_by_family": {f: round(_mean(v), 3) for f, v in sorted(fam.items())},
        "sdpo_minus_grpo": _mean(fam.get("sdpo", [])) - _mean(fam.get("grpo", [])),
        "spearman_length_coop": spearman([r["score"] for r in coop], [r["length"] for r in coop]),
        "spearman_overlap_coop": spearman([r["score"] for r in coop], [r["overlap"] for r in coop]),
        "seconds_per_256": seconds / len(rows) * 256,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--moral-value", default="deontological")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--policies", default="", help="comma-separated cells (default: all)")
    ap.add_argument("--per-prompt", type=int, default=16)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("judge_offline_out"))
    args = ap.parse_args()

    traces = load_traces(args.data, args.per_prompt,
                         [c for c in args.policies.split(",") if c] or None, args.seed)
    principle = get_moral_value(args.moral_value)
    for t in traces:
        t["length"] = len(t["response"].split())
        t["overlap"] = principle_overlap(t["response"], principle)
    print(f"{len(traces)} traces from {args.data.name} "
          f"({len({t['cell'] for t in traces})} policies, {args.per_prompt} per prompt)")

    args.out.mkdir(parents=True, exist_ok=True)
    summaries = []
    with open(args.out / "scores.jsonl", "w") as f:
        for name in args.variants.split(","):
            cfg = JudgeConfig.from_dict({"model": args.model, "moral_value": args.moral_value,
                                         "endpoint": args.endpoint, **VARIANTS[name]})
            prompts = [judge_prompt_from_text(t["game"], t["response"], args.moral_value, cfg.scoring)
                       for t in traces]
            t0 = time.time()
            scores = score_prompts(cfg, prompts)
            seconds = time.time() - t0
            rows = []
            for t, s in zip(traces, scores):
                row = {k: v for k, v in t.items() if k != "game"}
                row.update(variant=name, score=0.5 if s is None else s, invalid=float(s is None))
                rows.append(row)
                f.write(json.dumps(row) + "\n")
            summaries.append(summarize(name, rows, seconds))
            print(json.dumps(summaries[-1], indent=1), flush=True)

    (args.out / "summary.json").write_text(json.dumps(summaries, indent=1))
    cols = ["variant", "invalid_rate", "group_std_mean", "flat_group_share",
            "auc_coop_vs_exploit", "sdpo_minus_grpo", "spearman_length_coop",
            "spearman_overlap_coop", "seconds_per_256"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for s in summaries:
        lines.append("| " + " | ".join(
            s[c] if isinstance(s[c], str) else f"{s[c]:.3f}" for c in cols) + " |")
    lines.append("\nMean score by policy family:")
    lines += [f"- {s['variant']}: {s['mean_by_family']}" for s in summaries]
    (args.out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
