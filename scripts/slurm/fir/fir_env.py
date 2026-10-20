"""Python twin of fir_env.sh for scripts/slurm/fir/submit_*.py.

load_fir_env(): scripts/slurm/cluster.env (KEY=VALUE, no override of
variables already set; SLURM_ACCOUNT -> SBATCH_ACCOUNT, as the Clariden
submit scripts do) then scripts/slurm/fir/fir.env into os.environ.
sbatch_resource_args(kind): Fir resource flags for a "node" (whole node)
or "gpu" (one cell) job, spliced between "sbatch" and the launcher.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

FIR_DIR = Path(__file__).resolve().parent
SLURM_DIR = FIR_DIR.parent


def _read_kv(path: Path, override: bool) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        # ${VAR:-default} in fir.env: python's expandvars leaves it alone,
        # so resolve the two forms the file uses by hand.
        if value.startswith("${") and ":-" in value and value.endswith("}"):
            var, default = value[2:-1].split(":-", 1)
            value = os.environ.get(var) or default
        value = os.path.expandvars(value)
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def load_fir_env() -> None:
    env_file = SLURM_DIR / "cluster.env"
    if env_file.is_file():
        _read_kv(env_file, override=False)
    if os.environ.get("SLURM_ACCOUNT"):
        os.environ.setdefault("SBATCH_ACCOUNT", os.environ["SLURM_ACCOUNT"])
    os.environ.setdefault("HF_HOME", os.path.join(
        os.environ.get("SCRATCH", os.environ["HOME"]), ".cache/huggingface"))
    _read_kv(FIR_DIR / "fir.env", override=True)


def sbatch_resource_args(kind: str) -> List[str]:
    env = os.environ
    if kind == "node":
        return [*env["FIR_SBATCH_NODE_ARGS"].split(),
                f"--mem={env['FIR_NODE_MEM']}",
                f"--cpus-per-task={env['FIR_PACK_CPUS_PER_CELL']}"]
    if kind == "gpu":
        return env["FIR_SBATCH_GPU_ARGS"].split()
    raise ValueError(kind)
