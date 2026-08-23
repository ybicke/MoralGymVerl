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
      game: [prisoners_dilemma]    # game, moral_value, protocol are mandatory
      moral_value: [none, deontological]
      protocol: [single_round]     # pins the experimental phase; see below
      representation: [matrix, prose]
      model: [google/gemma-2-9b-it]          # HF id; base-model screening
    env:                           # constant per-job env toggles
      RUN_PROBES: "on"
    extra_args: ["--temperature", "0.7"]   # constant forwarded flags

Axis-to-launcher mapping (see cell_submission):
    game, moral_value  -> positionals 1, 2
    representation     -> REPRESENTATION env  \
    model              -> MODEL env            } env, because these must
    protocol           -> PROTOCOL env        /  reach the probes too
    anything else      -> forwarded flag --<axis-with-dashes> <value>

Forwarded flags reach `behavioral` ONLY (the launcher passes "${@:4}" to it
alone). An axis that must also apply to probe_a/probe_b therefore has to go
through _ENV_AXES — otherwise the probes silently keep the eval yaml's value
and the cell mixes two settings. model and protocol are exactly that case.

`protocol` is mandatory because it is what separates the experimental phases:
its preset pins num_rounds / game_design / opponents at submit time, so a
single-turn sweep cannot inherit the eval yaml's 5-round setting by omission.
A sweep listing only `single_round` is single-turn end to end (probe B's
episode mode additionally needs RUN_PROBE_B_EPISODE=on, off by default).
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Tuple

import yaml

from moralgym_verl.eval.config import PROTOCOL_PRESETS, resolve_presentation

LAUNCHER = "scripts/slurm/eval_teacher_signal.sh"
PACK_LAUNCHER = "scripts/slurm/eval_pack.sh"
MANIFEST_NAME = "sweep_manifest.json"
# Results root under eval_results/: the sweep's `results_dir` key.
# teacher_signal = pre-training screens (base models, principle in
# context); post_training = checkpoint evals of trained models. Keeping
# them apart is the point -- one folder answers "what does the base model
# do with the wording", the other "what did training install".
DEFAULT_RESULTS_DIR = "teacher_signal"


def results_dir(spec: Dict) -> str:
    return str(spec.get("results_dir", DEFAULT_RESULTS_DIR))
# Cells per packed job. 4 = one per GPU on a GH200 node. Clariden is
# OverSubscribe=EXCLUSIVE, so a 1-GPU job is billed for all 4 GPUs; packing
# cuts billed node-hours (and the fairshare hit that follows them) ~4x.
PACK_SIZE = 4
# Axes consumed by mechanisms other than forwarded flags.
_POSITIONAL_AXES = ("game", "moral_value")
# Axes that must reach the probes as well as behavioral: forwarded flags go
# to behavioral only, so anything that defines what the cell IS (which model,
# which turn structure, how the payoff block is rendered) travels by env.
_ENV_AXES = {"representation": "REPRESENTATION",
             "game_description": "GAME_DESCRIPTION",
             "model": "MODEL",
             "protocol": "PROTOCOL",
             # Trained-adapter cells: "base", an absolute adapter dir, or
             # "<run>/global_step_N" resolved by the launcher against
             # $CKPT_ROOT (the verl run layout, see train_verl.sh CKPT_DIR).
             "checkpoint": "CHECKPOINT"}
# Axes every sweep must declare, even single-valued. game/moral_value are the
# launcher's positionals; protocol is required so a sweep can never silently
# inherit the eval yaml's turn structure — the configs are 5-round, so an
# omitted protocol used to make every cell multi-round by accident. Declaring
# it makes the experimental phase explicit and puts it in the manifest.
_REQUIRED_AXES = _POSITIONAL_AXES + ("protocol",)


def load_sweep(path: str) -> Dict:
    with open(path) as f:
        spec = yaml.safe_load(f)
    for field in ("name", "axes"):
        if field not in spec:
            raise ValueError(f"sweep spec missing required field: {field}")
    for axis in _REQUIRED_AXES:
        if axis not in spec["axes"]:
            raise ValueError(f"sweep axes must include {axis} "
                             f"(single-valued list is fine)")
    for axis, values in spec["axes"].items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"axis {axis!r} must be a non-empty list, "
                             f"got {values!r}")
    unknown = [p for p in spec["axes"]["protocol"] if p not in PROTOCOL_PRESETS]
    if unknown:
        raise ValueError(f"unknown protocol(s) {unknown} in sweep axes; "
                         f"choose from {sorted(PROTOCOL_PRESETS)}")
    # Validate presentation specs at SUBMIT time: as a forwarded flag a
    # typo ('lables+role') would only surface once the cell is on a GPU.
    for pres in spec["axes"].get("presentation", []):
        resolve_presentation(pres)   # raises on an unknown preset/axis

    # The episode mode of probe B is the multi-round teacher-decay probe. A
    # sweep that turns it on while any cell is single-round would run an
    # instrument with no curve to measure. Caught here so --dry-run reports
    # it, rather than at job start once the cells are already queued.
    env = spec.get("env") or {}
    if str(env.get("RUN_PROBE_B_EPISODE", "off")).lower() == "on":
        single = [p for p in spec["axes"]["protocol"]
                  if PROTOCOL_PRESETS[p]["num_rounds"] < 2]
        if single:
            raise ValueError(
                f"env RUN_PROBE_B_EPISODE=on but protocol(s) {single} are "
                f"single-round; the episode probe measures per-round decay "
                f"and needs a multi-round protocol"
            )
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
    env = {"EVAL_GROUP": str(spec["eval_group"]),
           "RESULTS_DIR": results_dir(spec)}
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


def run_dir_stem(cell: Dict) -> str:
    """Directory name for one cell, minus the `_<jobid>` suffix.

    THE single implementation of cell naming. Under packing, four cells
    share one SLURM_JOB_ID, so the job id alone no longer separates them —
    the stem must. Cells are a cartesian product, so the axis tuple is
    unique by construction and therefore so is the stem.

    `<game>__<moral_value>__<other axes, yaml order>`; the launcher appends
    `_<jobid>` and never derives a name itself (two implementations that
    can disagree would mean two cells writing one directory).
    """
    # '/' would nest directories (checkpoint values are path-like).
    rest = [str(v).replace("/", "-") for axis, v in cell.items()
            if axis not in _POSITIONAL_AXES]
    stem = f"{cell['game']}__{cell['moral_value']}"
    return f"{stem}__{'__'.join(rest)}" if rest else stem


def pack_batches(spec: Dict, cells: List[Dict],
                 pack_size: int = PACK_SIZE) -> List[List[Dict]]:
    """Group cells into per-node batches, heaviest first.

    A batch holds the node until its SLOWEST cell finishes, so mixing a
    ~85min probe cell with a ~60min probe-less one wastes the difference on
    an idle GPU. Sorting by whether the cell runs probes clusters like with
    like; the only ragged batch is then at the boundary.
    """
    def has_probes(cell: Dict) -> bool:
        env = spec.get("env") or {}
        return (cell["moral_value"] != "none"
                and str(env.get("RUN_PROBES", "on")) != "off")

    ordered = sorted(cells, key=lambda c: not has_probes(c))
    return [ordered[i:i + pack_size]
            for i in range(0, len(ordered), pack_size)]


def batch_payload(spec: Dict, batch: List[Dict]) -> Dict:
    """The batch.json eval_pack.sh consumes: per cell, the resolved run-dir
    stem plus the same env/args the single-cell launcher would have used."""
    payload = []
    for cell in batch:
        env, argv = cell_submission(spec, cell)
        # argv = [sbatch, launcher, game, moral_value, num_episodes, *args]
        payload.append({
            "eval_group": str(spec["eval_group"]),
            "results_dir": results_dir(spec),
            "run_dir_stem": run_dir_stem(cell),
            "game": str(cell["game"]),
            "moral_value": str(cell["moral_value"]),
            "num_episodes": int(spec.get("num_episodes", 25)),
            "env": {k: v for k, v in env.items()
                    if k not in ("EVAL_GROUP", "RESULTS_DIR")},
            "args": argv[5:],
            "axes": {k: str(v) for k, v in cell.items()},
        })
    return {"cells": payload}
