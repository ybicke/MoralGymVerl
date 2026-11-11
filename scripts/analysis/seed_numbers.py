"""Number macros + summary table for the seed-replicate PGG transfer.

Same cells and pooling as seed_figures.py (seed-grid / seed-multi):
one value per seed = pooled contribution rate of that cell, reported as
mean, sample standard deviation and min-max over seeds. Base and
in-context (prompted) rows are single cells.

Intervals are 95% normal intervals, rate +- 1.96 standard errors, with
the EPISODE as the unit, never the decision: the rounds of one live-play
episode are dependent, so each episode enters as its own contribution
rate. Single-round cells are stratified by fabricated state (the
balanced design is fixed); round 1 pooled over co-players by co-player.
For a trained row the interval is that of the mean over its seeds and
carries evaluation noise only; the spread between training seeds is the
min-max. Where the episodes of a cell do not vary (a rate of 0 or 1)
the standard error would be 0, so the cell gets a Wilson interval on
its episode count instead. Intervals are clipped to [0, 1].

  numbers_<prefix>.tex   \\<prefix>SR<Row>{Mean,Std,Min,Max,Lo,Hi}      single round
                         \\<prefix>MR<Row><Opp>{Mean,Std,Min,Max,Lo,Hi} multi round
                         \\<prefix>MR<Row>RoundOne{...}            round 1, pooled
                         \\<prefix>...<Row>Vs<Ref>{Diff,Lo,Hi}     row mean minus
                             reference, in points, with its interval
                         \\<prefix>...<Row>Vs<Ref>Seeds            seeds whose own
                             difference interval excludes 0 on the side of Diff
  tables/seed_transfer.tex
  seed_intervals.json    every cell and comparison behind the macros

  python scripts/analysis/seed_numbers.py --model Qwen3-8B \\
      --step 200 --single-group ... --multi-group ... --out <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import mean, stdev, variance
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_figures import Cell, load_cells, split  # noqa: E402

ROWS = [("Base", "Base"), ("Prompted", "Prompted"),
        ("Grpo", "GRPO"), ("Sdpo", "SDPO")]
REFS, TRAINED = ("Base", "Prompted"), ("Grpo", "Sdpo")
LEVEL, Z = 95, 1.959964


def pct(x: float) -> int:
    return int(Decimal(str(100 * x)).quantize(0, ROUND_HALF_UP))


def camel(s: str) -> str:
    return "".join(w.capitalize() for w in s.split("_"))


def wilson(p: float, n: int) -> Tuple[float, float]:
    d = 1 + Z * Z / n
    mid = (p + Z * Z / 2 / n) / d
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / 4 / n / n) / d
    return max(0.0, mid - half), min(1.0, mid + half)


def normal(est: float, se: float) -> Tuple[float, float]:
    return est - Z * se, est + Z * se


def clip(lo: float, hi: float) -> Tuple[float, float]:
    return max(0.0, lo), min(1.0, hi)


class Sample:
    """One cell's rate for one quantity, with its standard error.

    strata: per stratum a list of (contributions, decisions) per episode.
    The rate pools all decisions; the standard error is the stratified
    one over per-episode rates, sqrt(sum_h w_h^2 s_h^2 / n_h) with w_h
    the stratum's share of decisions."""

    def __init__(self, cell: Cell, strata: List[List[Tuple[int, int]]]):
        self.seed, self.path = cell.seed, str(cell.path)
        self.n = sum(len(s) for s in strata)
        total = sum(n for s in strata for _, n in s)
        self.est = sum(c for s in strata for c, _ in s) / total
        var = sum((sum(n for _, n in s) / total) ** 2
                  * variance([c / n for c, n in s]) / len(s) for s in strata)
        if var > 0:
            self.se = math.sqrt(var)
            self.lo, self.hi = clip(*normal(self.est, self.se))
        else:                      # no spread between episodes: Wilson
            self.lo, self.hi = wilson(self.est, self.n)
            self.se = (self.hi - self.lo) / (2 * Z)


def strata(block: Dict, by_state: bool = False, first_round: bool = False):
    """One block's episodes as (contributions, decisions) pairs, grouped by
    fabricated state if by_state, else one stratum.

    Cells written before episodes recorded their fab_state get it from the
    balanced design, which cycles the states in order; either way the
    grouping must reproduce the per-state rates the cell stores."""
    states = list(block.get("state_conditioning") or {})
    groups = defaultdict(list)
    for i, e in enumerate(block["episode_moves"]):
        moves = e["agent"][:1] if first_round else e["agent"]
        key = None
        if by_state:
            key = ("({},{})".format(*e["fab_state"]) if "fab_state" in e
                   else states[i % len(states)])
        groups[key].append((sum(m == "C" for m in moves), len(moves)))
    if by_state:
        for k, g in groups.items():
            stored = block["state_conditioning"][k]
            rate = sum(c for c, _ in g) / sum(n for _, n in g)
            if len(g) != stored["n"] or abs(rate - stored["p_C"]) > 1e-9:
                raise SystemExit(f"episodes do not reproduce state {k} of the cell")
    return list(groups.values())


def checked(sample: Sample, stored: float) -> Sample:
    """The episode list must reproduce the rate the cell stores."""
    if abs(sample.est - stored) > 1e-9:
        raise SystemExit(f"episode_moves give {sample.est:.6f}, the cell stores "
                         f"{stored:.6f}: {sample.path}")
    return sample


def rows(cells, step, principle, sample) -> Dict[str, List[Sample]]:
    """-> {row: [Sample]} with one sample per seed (one for base rows)."""
    base, ctx, runs = split(cells, [step], principle)
    out = {"Base": [sample(c) for c in base.values()],
           "Prompted": [sample(c) for c in ctx.values()]}
    for (channel, _), seeds in runs.items():
        out[channel.split()[0].capitalize()] = [
            sample(c) for c in sorted(seeds, key=lambda c: c.seed)]
    for row in REFS:
        if len(out[row]) != 1:
            raise SystemExit(f"need exactly one {row} cell, found {len(out[row])}")
    return out


def row(samples: List[Sample]) -> Tuple[float, float]:
    """Mean over the row's cells and its standard error (cells independent)."""
    return (mean(s.est for s in samples),
            math.sqrt(sum(s.se ** 2 for s in samples)) / len(samples))


def stats(samples: List[Sample]) -> Dict[str, int]:
    vals = [s.est for s in samples]
    lo, hi = ((samples[0].lo, samples[0].hi) if len(samples) == 1
              else clip(*normal(*row(samples))))
    return {"Mean": pct(mean(vals)), "Std": pct(stdev(vals) if len(vals) > 1 else 0.0),
            "Min": pct(min(vals)), "Max": pct(max(vals)), "Lo": pct(lo), "Hi": pct(hi)}


def versus(samples: List[Sample], ref: List[Sample]) -> Dict[str, int]:
    """Row mean minus the reference row's mean, in points; Seeds counts
    the seeds whose own difference interval excludes 0 on that side."""
    ref_est, ref_se = row(ref)
    est, se = row(samples)
    diff = est - ref_est
    lo, hi = normal(diff, math.hypot(se, ref_se))
    clear = 0
    for s in samples:
        s_lo, s_hi = normal(s.est - ref_est, math.hypot(s.se, ref_se))
        clear += (s_lo > 0) if diff > 0 else (s_hi < 0)
    return {"Diff": pct(diff), "Lo": pct(lo), "Hi": pct(hi), "Seeds": int(clear)}


def cell(samples: List[Sample]) -> str:
    """Table entry: rate [interval] for a single cell, mean (min--max over
    seeds) for a trained row."""
    st = stats(samples)
    if len(samples) == 1:
        return f"{st['Mean']}{{\\scriptsize\\,[{st['Lo']},{st['Hi']}]}}"
    return f"{st['Mean']}{{\\scriptsize\\,({st['Min']}--{st['Max']})}}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--step", type=int, required=True)
    ap.add_argument("--principle", default="deontological")
    ap.add_argument("--single-group", action="append", required=True)
    ap.add_argument("--multi-group", action="append", required=True)
    ap.add_argument("--prefix", default="Seeds")
    ap.add_argument("--out", required=True, help="dir for generated/ files")
    a = ap.parse_args()

    scells = load_cells([Path(g) for g in a.single_group], "single_round", a.model)
    mcells = load_cells([Path(g) for g in a.multi_group], "multi_round", a.model)
    opps = list(next(iter(mcells)).blocks)
    quantities = {"SR{}": rows(
        scells, a.step, a.principle, lambda c: checked(
            Sample(c, strata(c.first, by_state=True)),
            c.first["cooperation_rate"]))}
    for o in opps:
        quantities[f"MR{{}}{camel(o)}"] = rows(
            mcells, a.step, a.principle, lambda c, o=o: checked(
                Sample(c, strata(c.blocks[o])), c.blocks[o]["cooperation_rate"]))
    # round 1 has no previous round (absent from training); co-players
    # cannot differ yet, so pool it over them
    quantities["MR{}RoundOne"] = rows(
        mcells, a.step, a.principle, lambda c: checked(
            Sample(c, [s for o in opps for s in strata(c.blocks[o], first_round=True)]),
            sum(c.blocks[o]["per_round"]["round_1"]["p_C"] for o in opps) / len(opps)))
    single = quantities["SR{}"]

    # The numbers below describe ONE design; mixed sizes would mislabel it.
    def design(samples_by_row, what) -> int:
        sizes = {s.n for samples in samples_by_row.values() for s in samples}
        if len(sizes) != 1:
            raise SystemExit(f"{what} cells differ in episode count: {sorted(sizes)}")
        return sizes.pop()
    sr_eps = design(single, "single-round")
    mr_eps = design(quantities[f"MR{{}}{camel(opps[0])}"], "multi-round")
    sr_states = len(scells[0].first["state_conditioning"])
    mr_rounds = len(mcells[0].first["per_round"])

    p = a.prefix
    n = len(single["Sdpo"])
    macros = {f"{p}Step": str(a.step), f"{p}N": str(n)}
    report = {"interval": "normal over episodes, Wilson without spread", "level": LEVEL, "step": a.step, "quantities": {}}
    for q, by_row in quantities.items():
        entry = report["quantities"][q.format("")] = {"rows": {}, "versus": {}}
        for r, _ in ROWS:
            st = stats(by_row[r])
            for k, v in st.items():
                macros[f"{p}{q.format(r)}{k}"] = str(v)
            entry["rows"][r] = {**st, "cells": [
                {"seed": s.seed, "episodes": s.n, "rate": round(100 * s.est, 2),
                 "se": round(100 * s.se, 2),
                 "lo": round(100 * s.lo, 2), "hi": round(100 * s.hi, 2),
                 "path": s.path} for s in by_row[r]]}
        pairs = [(r, f) for r in TRAINED for f in REFS] + [("Sdpo", "Grpo")]
        for r, f in pairs:
            vs = versus(by_row[r], by_row[f])
            if f in TRAINED:               # two trained rows: seeds are not paired
                vs.pop("Seeds")
            for k, v in vs.items():
                macros[f"{p}{q.format(r)}Vs{f}{k}"] = str(v)
            entry["versus"][f"{r} - {f}"] = vs
    for r in TRAINED:              # highest single-seed value over co-players
        macros[f"{p}MR{r}Max"] = str(max(
            stats(quantities[f"MR{{}}{camel(o)}"][r])["Max"] for o in opps))

    out = Path(a.out)
    gen = out / "generated"
    (gen / "tables").mkdir(parents=True, exist_ok=True)
    lines = ["% generated by scripts/analysis/seed_numbers.py -- do not edit"]
    lines += [rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(macros.items())]
    (gen / f"numbers_{p}.tex").write_text("\n".join(lines) + "\n")
    (out / "seed_intervals.json").write_text(json.dumps(report, indent=1) + "\n")

    short = {"full_contributor": "Full", "free_rider": "Free", "noisy_conditional": "Noisy"}
    heads = " & ".join(short.get(o, o) for o in opps)
    tab = [
        "% generated by scripts/analysis/seed_numbers.py -- do not edit",
        r"\begin{table}[t]", r"\centering\small", r"\setlength{\tabcolsep}{3pt}",
        rf"\begin{{tabular}}{{@{{}}l c {'c' * len(opps)}@{{}}}}", r"\toprule",
        rf" & Single & \multicolumn{{{len(opps)}}}{{c}}{{{mr_rounds} rounds vs.}} \\",
        rf"\cmidrule(lr){{3-{2 + len(opps)}}}",
        rf" & round & {heads} \\", r"\midrule",
    ]
    for r, label in ROWS:
        tab.append(f"{label} & {cell(single[r])} & " + " & ".join(
            cell(quantities[f'MR{{}}{camel(o)}'][r]) for o in opps) + r" \\")
    tab += [r"\bottomrule", r"\end{tabular}",
            rf"\caption{{Contribution rate (\%) of {a.model} in the public-goods "
            rf"game after {a.step} training steps (protocols in "
            rf"Section~\ref{{sec:games:protocol}}). Single round pools "
            rf"{sr_eps} decisions over the {sr_states} fabricated states, "
            rf"{mr_rounds} rounds pools all rounds of {mr_eps} episodes against "
            rf"a full contributor, free rider or noisy conditional co-player. "
            rf"Only Prompted has the principle in the prompt. Base and Prompted "
            rf"give the rate with its {LEVEL}\% confidence interval over episodes "
            rf"in brackets, trained rows the mean over {n} seeds with the lowest "
            rf"and highest seed in parentheses.}}",
            r"\label{tab:seed-transfer}", r"\end{table}"]
    (gen / "tables" / "seed_transfer.tex").write_text("\n".join(tab) + "\n")
    print(f"wrote {gen}/numbers_{p}.tex ({len(macros)} macros), "
          f"{gen}/tables/seed_transfer.tex, {out}/seed_intervals.json")


if __name__ == "__main__":
    main()
