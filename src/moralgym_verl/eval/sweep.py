"""Sweep declaration and grid expansion for eval campaigns.

A sweep YAML declares one experiment: the comparison axes and the
constants. scripts/slurm/submit_sweep.py expands it, submits one
eval_teacher_signal.sh job per cell, and writes sweep_manifest.json into
the group's results directory.

Naming contract (docs/naming.md): the yaml's PATH is the experiment's
identity, and the results land at the mirrored path —
    configs/eval/teacher_signal/<model>/<family>/<experiment>.yaml
    configs/eval/post_training/<run_name>/<family>/<experiment>.yaml
    -> eval_results/<results_root>/<subject>/<family>/<experiment>/
<subject> is a base-model token (teacher_signal); under post_training it
is either a training RUN_NAME (that run's evals on its training game) or a
bare model token (model-level sweeps: held-out games, or several runs of
the model side by side); <family> is a game family (GAME_FAMILIES,
mirroring src/moralgym_verl/game/). eval_group and results_dir are DERIVED
from the path; a spec declaring either is refused, so config and results
location cannot diverge. The measurement profile (harness) is likewise
derived: configs/eval/harness/<model>/<family>.yaml.

Spec schema:
    config: configs/eval/harness/<model>/<family>.yaml   # optional override;
                                   # default derived from the subject's model token
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
from pathlib import Path
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
RESULTS_ROOTS = ("teacher_signal", "post_training")
# Canonical model tokens (docs/naming.md). A teacher_signal subject is one
# of these; a post_training subject is a run name starting with one, or the
# bare token for model-level sweeps (held-out games / several runs).
MODEL_TOKENS = ("gemma2_9b", "gemma3_12b", "llama31_8b", "qwen3_8b",
                "qwen3_32b")
# Game families, mirroring src/moralgym_verl/game/ (classic_games.py = 2x2
# matrix games, pgg_game.py = n-player). The family is a path level between
# subject and experiment; a sweep's game axis must stay inside its family.
GAME_FAMILIES = {
    "classic": ("prisoners_dilemma", "stag_hunt", "chicken"),
    "pgg": ("public_goods",),
}
SWEEP_CONFIG_ROOT = "configs/eval"
# Harness profiles: the model x game-family MEASUREMENT settings (HF id,
# generation budget, prompt regime, presentation defaults). A harness carries
# no experiment content -- no moral value, no checkpoint, no grid -- so it
# lives outside both results roots, and every sweep of a model, base screen
# or checkpoint eval, resolves to the same file. That shared file is what
# makes base rows and trained rows comparable.
HARNESS_ROOT = f"{SWEEP_CONFIG_ROOT}/harness"
_REPO_ROOT = Path(__file__).resolve().parents[3]


def harness_config(model_token: str, family: str) -> str:
    """Repo-relative path of the harness profile:
    configs/eval/harness/<model>/<family>.yaml. Raises if absent — a model
    without a profile cannot be measured, and a sweep must not silently
    borrow another model's settings."""
    path = f"{HARNESS_ROOT}/{model_token}/{family}.yaml"
    if not (_REPO_ROOT / path).exists():
        raise ValueError(f"no harness profile for {model_token}/{family}: "
                         f"expected {path}")
    return path


def results_dir(spec: Dict) -> str:
    return str(spec["results_dir"])


def sweep_identity(path: str) -> Tuple[str, str, str, str]:
    """(results_root, subject, family, experiment) from the yaml's location.

    The path IS the identity:
    configs/eval/<root>/<subject>/<family>/<exp>.yaml. Raising here
    (rather than defaulting) is what makes a misplaced spec unsubmittable
    instead of silently creating a new results location.
    """
    parts = Path(path).resolve().parts
    if len(parts) < 6 or parts[-6:-4] != ("configs", "eval"):
        raise ValueError(
            f"sweep spec must live at configs/eval/<results_root>/<subject>/"
            f"<family>/<experiment>.yaml (docs/naming.md), got: {path}"
        )
    root, subject, family = parts[-4], parts[-3], parts[-2]
    if root not in RESULTS_ROOTS:
        raise ValueError(f"results root {root!r} must be one of "
                         f"{RESULTS_ROOTS}, got path: {path}")
    if family not in GAME_FAMILIES:
        raise ValueError(f"game family {family!r} must be one of "
                         f"{sorted(GAME_FAMILIES)} (src/moralgym_verl/game/),"
                         f" got path: {path}")
    return root, subject, family, Path(path).stem


def subject_model(root: str, subject: str) -> str:
    """The model token a subject refers to; raises if none matches."""
    if root == "teacher_signal":
        if subject not in MODEL_TOKENS:
            raise ValueError(f"teacher_signal subject {subject!r} must be a "
                             f"model token: {MODEL_TOKENS}")
        return subject
    for token in MODEL_TOKENS:
        if subject == token or subject.startswith(token + "_"):
            return token
    raise ValueError(f"post_training subject {subject!r} must be a training "
                     f"run name (<model>_<size>_...) or a bare model token "
                     f"(model-level sweep); known tokens: {MODEL_TOKENS}")
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
    root, subject, family, experiment = sweep_identity(path)
    model_token = subject_model(root, subject)
    for field in ("name", "eval_group", "results_dir"):
        if field in spec:
            raise ValueError(
                f"sweep spec declares {field!r}, but identity is derived "
                f"from the path (docs/naming.md) — delete the field; this "
                f"spec resolves to "
                f"eval_results/{root}/{subject}/{family}/{experiment}"
            )
    spec["name"] = f"{subject}/{family}/{experiment}"
    spec["eval_group"] = f"{subject}/{family}/{experiment}"
    spec["results_dir"] = root
    if "axes" not in spec:
        raise ValueError("sweep spec missing required field: axes")
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
    # The family dir claims a game family; the axis must not leave it —
    # otherwise "which folder holds the PGG results" gets a wrong answer.
    foreign = [g for g in spec["axes"]["game"]
               if g not in GAME_FAMILIES[family]]
    if foreign:
        raise ValueError(f"game(s) {foreign} are not in family {family!r} "
                         f"({GAME_FAMILIES[family]})")

    # Checkpoint axis: only post_training evaluates trained weights. A
    # run-named subject IS the training run — every checkpoint must belong
    # to it (the machine-checked training<->eval link); a model-level
    # subject accepts any run of that model.
    checkpoints = [c for c in spec["axes"].get("checkpoint", [])
                   if c != "base" and not str(c).startswith("/")]
    if checkpoints and root != "post_training":
        raise ValueError("checkpoint axis is only valid under "
                         "configs/eval/post_training/")
    if root == "post_training":
        if subject == model_token:
            stray = [c for c in checkpoints
                     if not str(c).startswith(model_token + "_")]
            where = f"a {model_token} training run"
        else:
            stray = [c for c in checkpoints
                     if not str(c).startswith(subject + "/")]
            where = (f"training run {subject!r} (use post_training/"
                     f"{model_token}/ for sweeps across runs)")
        if stray:
            raise ValueError(f"checkpoint(s) {stray} do not belong to {where}")

    spec.setdefault("config", harness_config(model_token, family))
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
