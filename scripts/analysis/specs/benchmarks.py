#!/usr/bin/env python3.11
"""Results document for external benchmarks on checkpoints (retention).

One row per checkpoint, base first. Table T1 is the headline per
benchmark (accuracy, binomial s.e., difference to base); T2 breaks MMLU
into its four categories; T3 reports GSM8K response statistics from the
samples (length, answer-line rate), the cheap signal of a changed output
habit. Numbers are read from lm-eval's results JSON per benchmark
(docs/external_benchmarks.md fixes the protocol).

    /usr/bin/python3.11 scripts/analysis/make_results.py \\
        eval_results/benchmarks/qwen3_8b/retention/seed1
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from results_doc import MISSING, Cell, Table, to_latex, to_markdown  # noqa: E402

# benchmark -> (results key, metric key, n-samples key)
HEADLINE = {"gsm8k": ("gsm8k_cot_llama", "exact_match,strict-match", "gsm8k_cot_llama"),
            "mmlu": ("mmlu", "acc,none", None)}
MMLU_GROUPS = ("mmlu_stem", "mmlu_humanities", "mmlu_social_sciences", "mmlu_other")
CKPT_RE = re.compile(r"^(.+)/global_step_(\d+|final)$")
GSM8K_LINE = re.compile(r"The final answer is")


class Row:
    __slots__ = ("label", "run", "step", "cell_dir", "meta", "results", "job")

    def __init__(self, cell_dir: Path):
        self.cell_dir = cell_dir
        self.meta = json.loads((cell_dir / "cell.json").read_text())
        m = CKPT_RE.match(self.meta["checkpoint"])
        self.run, self.step = (m.group(1), m.group(2)) if m else ("base", "")
        # Row label: run name without the model prefix, then the step.
        short = re.sub(r"^[a-z0-9]+_[0-9]+b_", "", self.run)
        self.label = "base" if not m else f"{short} s{self.step}"
        self.job = cell_dir.name.rsplit("_", 1)[-1]
        self.results = {b: _load_results(cell_dir / b) for b in self.meta["benchmarks"]}


def _load_results(bench_dir: Path) -> Optional[Dict]:
    files = sorted(glob.glob(str(bench_dir / "**" / "results_*.json"), recursive=True))
    return json.loads(Path(files[-1]).read_text()) if files else None


def metric(res: Optional[Dict], key: str, name: str) -> Tuple[Optional[float], Optional[float]]:
    """(value, stderr) of one metric, or (None, None)."""
    if not res or key not in res["results"]:
        return None, None
    r = res["results"][key]
    base, suffix = name.split(",", 1)
    return r.get(name), r.get(f"{base}_stderr,{suffix}")


def fmt(v: Optional[float], se: Optional[float] = None) -> str:
    if v is None:
        return MISSING
    return f"{100 * v:.1f}" + (f" ± {100 * se:.1f}" if se is not None else "")


def delta(v: Optional[float], ref: Optional[float]) -> Cell:
    if v is None or ref is None:
        return Cell(MISSING)
    d = 100 * (v - ref)
    return Cell(f"{d:+.1f}", bold=abs(d) >= 2.0)


def headline_table(rows: List[Row], base: Optional[Row]) -> Table:
    cols = [("GSM8K", ["acc ± s.e.", "Δ base"]), ("MMLU", ["acc ± s.e.", "Δ base"])]
    body = []
    for r in rows:
        cells = []
        for bench, (key, name, _) in HEADLINE.items():
            v, se = metric(r.results.get(bench), key, name)
            ref = metric(base.results.get(bench), key, name)[0] if base else None
            cells += [Cell(fmt(v, se)), delta(v, ref) if r is not base else Cell("")]
        body.append((r.label, cells))
    n = {b: _n_samples(rows, b) for b in HEADLINE}
    return Table(
        key="T1-retention", title="Table T1 — capability retention",
        caption=("Accuracy in percent with binomial standard error; Δ base is "
                 "the difference to the untrained row in points, bold at 2 "
                 "points or more. "
                 f"GSM8K n = {n['gsm8k']} (8-shot chain of thought as chat "
                 f"turns, exact match on the final number); MMLU n = "
                 f"{n['mmlu']} (5-shot log-likelihood over the answer "
                 "letters, size-weighted over 57 subjects). Greedy decoding, "
                 "thinking off."),
        stub="checkpoint", col_groups=cols, panels=[(None, body)],
        subtitle="Stock lm-eval tasks gsm8k_cot_llama and mmlu; base = same "
                 "protocol without an adapter.")


def _n_samples(rows: List[Row], bench: str) -> str:
    for r in rows:
        res = r.results.get(bench)
        if res:
            ns = res.get("n-samples") or {}
            key = HEADLINE[bench][2]
            if key and key in ns:
                return str(ns[key].get("effective", ns[key].get("original")))
            return str(sum(v.get("effective", 0) for v in ns.values()))
    return "?"


def mmlu_table(rows: List[Row], base: Optional[Row]) -> Table:
    names = [g.replace("mmlu_", "").replace("_", " ") for g in MMLU_GROUPS]
    body = []
    for r in rows:
        cells = []
        for g in MMLU_GROUPS:
            v, se = metric(r.results.get("mmlu"), g, "acc,none")
            ref = metric(base.results.get("mmlu"), g, "acc,none")[0] if base else None
            d = "" if r is base or v is None or ref is None else f" ({100 * (v - ref):+.1f})"
            cells.append(Cell(fmt(v) + d))
        body.append((r.label, cells))
    return Table(
        key="T2-mmlu-categories", title="Table T2 — MMLU by category",
        caption="Accuracy in percent per MMLU category, difference to base in "
                "parentheses.",
        stub="checkpoint", col_groups=[("MMLU category", names)],
        panels=[(None, body)])


def gsm8k_stats(cell_dir: Path) -> Optional[Dict[str, float]]:
    files = sorted(glob.glob(str(cell_dir / "gsm8k" / "**" / "samples_gsm8k_*.jsonl"),
                        recursive=True))
    if not files:
        return None
    lens, lines, flex = [], 0, 0
    with open(files[-1]) as f:
        for line in f:
            s = json.loads(line)
            if s.get("filter") != "strict-match":
                continue
            resp = s["resps"][0][0]
            lens.append(len(resp.split()))
            lines += bool(GSM8K_LINE.search(resp))
    n = len(lens)
    if not n:
        return None
    return {"n": n, "words": sum(lens) / n, "max_words": max(lens), "line": lines / n}


def gsm8k_table(rows: List[Row]) -> Table:
    body = []
    for r in rows:
        st = gsm8k_stats(r.cell_dir)
        if not st:
            body.append((r.label, [Cell(MISSING)] * 3))
            continue
        v_s = metric(r.results.get("gsm8k"), "gsm8k_cot_llama", "exact_match,strict-match")[0]
        v_f = metric(r.results.get("gsm8k"), "gsm8k_cot_llama", "exact_match,flexible-extract")[0]
        body.append((r.label, [Cell(f"{st['words']:.0f} (max {st['max_words']})"),
                               Cell(f"{100 * st['line']:.1f}"),
                               Cell(fmt(v_f) if v_f is not None else MISSING)]))
    return Table(
        key="T3-gsm8k-responses", title="Table T3 — GSM8K response statistics",
        caption="Mean and maximum response length in words, share of "
                "responses that end with the required answer line, and "
                "accuracy under the lenient last-number extraction. A length "
                "collapse or a missing answer line flags a changed output "
                "habit rather than lost ability.",
        stub="checkpoint",
        col_groups=[("GSM8K responses", ["words", "answer line %", "acc flexible"])],
        panels=[(None, body)])


def header(rows: List[Row], group: Path) -> str:
    manifest = {}
    if (group / "sweep_manifest.json").exists():
        manifest = json.loads((group / "sweep_manifest.json").read_text())
    first = next((res for r in rows for res in r.results.values() if res), {})
    lines = [f"# Capability retention: {group.parent.parent.name} / {group.name}", ""]
    lines.append(f"{rows[0].meta['base_model']}. Checkpoints evaluated on external "
                 "benchmarks under one fixed protocol (docs/external_benchmarks.md): "
                 f"lm-eval {first.get('lm_eval_version', '?')}, harness "
                 f"`{rows[0].meta['config']}`, git `{manifest.get('git_commit', '?')}`, "
                 f"submitted {manifest.get('submitted', '?')[:10]}.")
    lines += ["", "| row | checkpoint | job |", "|---|---|---|"]
    lines += [f"| {r.label} | `{r.meta['checkpoint']}` | {r.job} |" for r in rows]
    lines += ["", "Generated by `scripts/analysis/make_results.py` (LaTeX in `tex/`).", ""]
    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def build(args: argparse.Namespace) -> Path:
    """External benchmarks on checkpoints: headline retention table, MMLU
    categories, GSM8K response statistics."""
    group = args.paths[0]
    cell_dirs = sorted(p.parent for p in group.glob("cells/*/cell.json"))
    if not cell_dirs:
        raise SystemExit(f"{group}: no benchmark cells (cells/*/cell.json)")
    rows = [Row(d) for d in cell_dirs]
    base = next((r for r in rows if r.run == "base"), None)
    rows.sort(key=lambda r: (r.run != "base", r.run, int(r.step) if r.step.isdigit() else 10**9))
    if base is None:
        print("WARNING: no base row; Δ columns are empty")

    out_dir = args.out or (group / "analysis")
    tex_dir = out_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)

    def emit(table: Table) -> str:
        (tex_dir / f"{table.key.replace('-', '_')}.tex").write_text(to_latex(table))
        return to_markdown(table)

    md = [header(rows, group), emit(headline_table(rows, base)),
          emit(mmlu_table(rows, base)), emit(gsm8k_table(rows))]
    path = out_dir / f"results_{group.name}.md"
    path.write_text("\n".join(md))
    print(f"saved -> {path}")
    return path
