"""Sweep declaration and grid expansion for eval campaigns.

A sweep YAML (configs/sweeps/*.yaml) declares one experiment: the
comparison axes and the constants. scripts/slurm/submit_sweep.py expands
it, submits one eval_teacher_signal.sh job per cell, and writes
sweep_manifest.json into the group's results directory.

Spec schema:
    name: tier1_pd                 # required; also the default eval_group
    config: configs/eval/teacher_signal_9b.yaml   # optional (launcher default)
    eval_group: tier1              # results subdir (default: name)
    num_episodes: 100              # 3rd positional (default: launcher's 25)
    axes:                          # required; grid = cartesian product
      game: [prisoners_dilemma]    # game + moral_value are mandatory axes
      moral_value: [none, deontological]
      representation: [matrix, prose]
      protocol: [single_round, multi_round]
    env:                           # constant per-job env toggles
      RUN_PROBES: "on"
    extra_args: ["--temperature", "0.7"]   # constant forwarded flags

Axis-to-launcher mapping (see cell_submission):
    game, moral_value  -> positionals 1, 2
    representation     -> REPRESENTATION env (reaches behavioral AND probes)
    anything else      -> forwarded flag --<axis-with-dashes> <value>
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Tuple

import yaml

LAUNCHER = "scripts/slurm/eval_teacher_signal.sh"
MANIFEST_NAME = "sweep_manifest.json"
# Axes consumed by mechanisms other than forwarded flags.
_POSITIONAL_AXES = ("game", "moral_value")
_ENV_AXES = {"representation": "REPRESENTATION"}


def load_sweep(path: str) -> Dict:
    with open(path) as f:
        spec = yaml.safe_load(f)
    for field in ("name", "axes"):
        if field not in spec:
            raise ValueError(f"sweep spec missing required field: {field}")
    for axis in _POSITIONAL_AXES:
        if axis not in spec["axes"]:
            raise ValueError(f"sweep axes must include {axis} "
                             f"(single-valued list is fine)")
    for axis, values in spec["axes"].items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"axis {axis!r} must be a non-empty list, "
                             f"got {values!r}")
    spec.setdefault("eval_group", spec["name"])
    return spec


def expand_cells(spec: Dict) -> List[Dict]:
    """Cartesian product of the axes, in the YAML's axis order (stable:
    last axis varies fastest)."""
    names = list(spec["axes"])
    return [dict(zip(names, combo))
            for combo in itertools.product(*(spec["axes"][a] for a in names))]


def cell_submission(spec: Dict, cell: Dict) -> Tuple[Dict[str, str], List[str]]:
    """(env_overrides, argv) to submit one cell via sbatch.

    Everything routes through the existing launcher so a sweep cell is
    byte-identical to a hand-submitted job with the same settings.
    """
    env = {"EVAL_GROUP": str(spec["eval_group"])}
    for key, value in (spec.get("env") or {}).items():
        env[str(key)] = str(value)

    # Probes are protocol-independent (fixed presentation, fabricated
    # states), so when protocol is a real axis they'd rerun identically in
    # every protocol cell. Run them only in the FIRST listed protocol's
    # cells; the others get RUN_PROBES=off.
    protocols = spec["axes"].get("protocol") or []
    if len(protocols) > 1 and cell.get("protocol") != protocols[0]:
        env["RUN_PROBES"] = "off"

    argv = ["sbatch", LAUNCHER, str(cell["game"]), str(cell["moral_value"]),
            str(spec.get("num_episodes", 25))]
    for axis, value in cell.items():
        if axis in _POSITIONAL_AXES:
            continue
        if axis in _ENV_AXES:
            env[_ENV_AXES[axis]] = str(value)
        else:
            argv += [f"--{axis.replace('_', '-')}", str(value)]
    if spec.get("config"):
        # Launcher reads CONFIG from env only if exported; it currently
        # hardcodes the teacher_signal yaml — pass through env for override.
        env["CONFIG"] = str(spec["config"])
    argv += [str(a) for a in (spec.get("extra_args") or [])]
    return env, argv
