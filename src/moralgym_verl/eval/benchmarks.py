"""External-benchmark sweeps: lm-eval tasks on checkpoints.

A spec names checkpoints, nothing else; its path is its identity
(docs/naming.md): configs/eval/benchmarks/<model>/<family>/<experiment>.yaml
-> eval_results/benchmarks/<model>/<family>/<experiment>/. The family is a
set of benchmarks measured together (FAMILIES); their protocols live in the
harness profile configs/eval/harness/<model>/<family>.yaml, so base rows
and trained rows share one protocol. One cell = one checkpoint = one GPU,
every benchmark of the family run in sequence.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from moralgym_verl.eval.sweep import MODEL_TOKENS, PACK_SIZE, harness_config

ROOT = "benchmarks"
PACK_LAUNCHER = "scripts/slurm/benchmark_pack.sh"
MANIFEST_NAME = "sweep_manifest.json"
# family -> benchmarks, in run order. Each needs an entry in the harness.
FAMILIES = {"retention": ("gsm8k", "mmlu")}
# lm-eval flags a harness benchmark entry may set (True -> bare flag,
# other values -> flag with value). Anything else in the entry is refused.
_FLAGS = ("num_fewshot", "apply_chat_template", "fewshot_as_multiturn",
          "gen_kwargs", "limit")


def spec_identity(path: str) -> Tuple[str, str, str]:
    """(model, family, experiment) from
    configs/eval/benchmarks/<model>/<family>/<experiment>.yaml."""
    parts = Path(path).resolve().parts
    if len(parts) < 6 or parts[-6:-3] != ("configs", "eval", ROOT):
        raise ValueError(f"benchmark spec must live at configs/eval/{ROOT}/"
                         f"<model>/<family>/<experiment>.yaml, got: {path}")
    model, family = parts[-3], parts[-2]
    if model not in MODEL_TOKENS:
        raise ValueError(f"subject {model!r} is not a model token "
                         f"{MODEL_TOKENS}, got path: {path}")
    if family not in FAMILIES:
        raise ValueError(f"family {family!r} must be one of "
                         f"{sorted(FAMILIES)}, got path: {path}")
    return model, family, Path(path).stem


def load_spec(path: str) -> Dict:
    with open(path) as f:
        spec = yaml.safe_load(f) or {}
    model, family, experiment = spec_identity(path)
    for field in ("name", "eval_group", "results_dir", "config"):
        if field in spec:
            raise ValueError(f"spec declares {field!r}, but identity and "
                             f"harness derive from the path; delete it")
    checkpoints = spec.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("spec needs a non-empty `checkpoints` list")
    if len(set(map(str, checkpoints))) != len(checkpoints):
        raise ValueError("duplicate checkpoint in spec")
    stray = [c for c in checkpoints
             if c != "base" and not str(c).startswith("/")
             and not str(c).startswith(model + "_")]
    if stray:
        raise ValueError(f"checkpoint(s) {stray} are not from a {model} "
                         f"training run")
    benchmarks = spec.get("benchmarks", list(FAMILIES[family]))
    unknown = [b for b in benchmarks if b not in FAMILIES[family]]
    if unknown:
        raise ValueError(f"benchmark(s) {unknown} are not in family "
                         f"{family!r} {FAMILIES[family]}")
    spec["benchmarks"] = list(benchmarks)
    spec["name"] = spec["eval_group"] = f"{model}/{family}/{experiment}"
    spec["results_dir"] = ROOT
    spec["config"] = harness_config(model, family)
    return spec


def load_harness(path: str) -> Dict:
    with open(path) as f:
        harness = yaml.safe_load(f)
    for key in ("model_name", "vllm", "benchmarks"):
        if key not in harness:
            raise ValueError(f"harness {path} missing {key!r}")
    for name, entry in harness["benchmarks"].items():
        if "tasks" not in entry:
            raise ValueError(f"harness benchmark {name!r} needs `tasks`")
        bad = [k for k in entry if k != "tasks" and k not in _FLAGS]
        if bad:
            raise ValueError(f"harness benchmark {name!r}: unknown key(s) "
                             f"{bad}; allowed {_FLAGS}")
    return harness


def expand_cells(spec: Dict) -> List[Dict]:
    return [{"checkpoint": str(c)} for c in spec["checkpoints"]]


def run_dir_stem(cell: Dict) -> str:
    """Cell directory name minus `_<jobid>`; '/' would nest."""
    return cell["checkpoint"].replace("/", "-")


def pack_batches(cells: List[Dict], pack_size: int = PACK_SIZE) -> List[List[Dict]]:
    return [cells[i:i + pack_size] for i in range(0, len(cells), pack_size)]


def batch_payload(spec: Dict, batch: List[Dict]) -> Dict:
    """The batch.json benchmark_pack.sh consumes."""
    return {"cells": [{
        "eval_group": spec["eval_group"],
        "results_dir": spec["results_dir"],
        "run_dir_stem": run_dir_stem(cell),
        "checkpoint": cell["checkpoint"],
        "benchmarks": list(spec["benchmarks"]),
        "config": spec["config"],
    } for cell in batch]}


def resolve_checkpoint(value: str, ckpt_root: str) -> Tuple[str, str]:
    """('base', '') | ('adapter', dir) | ('weights', dir).

    'base', an absolute dir, or '<run>/global_step_N' under ckpt_root in
    the verl layout, which holds actor/lora_adapter (LoRA runs) or
    actor/huggingface (full weights). Missing both is an error here, at
    job start, not one benchmark in."""
    if value == "base":
        return "base", ""
    if value.startswith("/"):
        kind = "adapter" if os.path.isfile(f"{value}/adapter_config.json") else "weights"
        return kind, value
    actor = f"{ckpt_root}/{value}/actor"
    if os.path.isfile(f"{actor}/lora_adapter/adapter_config.json"):
        return "adapter", f"{actor}/lora_adapter"
    if os.path.isfile(f"{actor}/huggingface/config.json"):
        return "weights", f"{actor}/huggingface"
    raise FileNotFoundError(f"checkpoint {value!r}: neither {actor}/"
                            f"lora_adapter nor {actor}/huggingface holds "
                            f"weights")


def lm_eval_command(harness: Dict, benchmark: str, kind: str, path: str,
                    out_dir: str) -> List[str]:
    """argv of one lm-eval run: the harness vLLM settings, the checkpoint
    (base model, adapter on it, or full weights), the benchmark's flags."""
    entry = harness["benchmarks"][benchmark]
    pretrained = path if kind == "weights" else harness["model_name"]
    model_args = [f"pretrained={pretrained}"]
    model_args += [f"{k}={v}" for k, v in harness["vllm"].items()]
    if kind == "adapter":
        model_args += [f"lora_local_path={path}",
                       f"max_lora_rank={harness.get('max_lora_rank', 32)}"]
    argv = ["python3", "-m", "lm_eval", "--model", "vllm",
            "--model_args", ",".join(model_args),
            "--tasks", str(entry["tasks"])]
    for flag in _FLAGS:
        value = entry.get(flag)
        if value is True:
            argv.append(f"--{flag}")
        elif value not in (None, False):
            argv += [f"--{flag}", str(value)]
    argv += ["--batch_size", "auto", "--log_samples",
             "--output_path", out_dir]
    return argv
