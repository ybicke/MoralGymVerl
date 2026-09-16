#!/bin/bash
# =============================================================================
# Submit a MoralGymVerl GRPO or SDPO training job on Clariden.
#
# Usage:
#   bash train_verl.sh <config_name> [run_name] [verl overrides...]
#
# Naming contract (docs/naming.md): configs/training/<RUN_NAME>.yaml is one
# file per run, named with the canonical grammar
#   <model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>[_vN]
# so RUN_NAME defaults to <config_name> and everything downstream (W&B,
# checkpoint dir, logs, dataset dir, post-training eval subject) shares it.
# DATASET_CONFIG defaults to the <algo>_<game>_<arm>_<opponent> fields of
# the run name. Pass [run_name] only to deviate (smokes: *_smoke).
#
# Examples:
#   bash train_verl.sh qwen3_8b_grpo_pd_util_tft_150
#   DRY_RUN=1 bash train_verl.sh qwen3_8b_grpo_pd_util_tft_150   # resolve only
#
# Trailing args become Hydra overrides. Env: PARTITION (normal), TIME
# (12:00:00), DATASET_CONFIG (derived, also names the W&B group),
# DATASET_SEED (42), DRY_RUN (stop after name/phase checks; no dataset
# generation, no sbatch).
#
# Three execution contexts — every path below depends on which one it is in:
#   1-4  login node, submit time   python3.11, no GPUs
#   5-7  string building only      nothing executes yet
#   8    compute node             training INSIDE the EDF container,
#                                 stage-out outside it (7)
# =============================================================================
set -euo pipefail

# ── 1. Arguments ─────────────────────────────────────────────────────────────
CONFIG_NAME="${1:?Usage: train_verl.sh <config_name> [run_name] [overrides...]}"
shift
# Optional run_name: a Hydra override always contains '=', a run name never.
if [ $# -gt 0 ] && [[ "$1" != *=* ]]; then
    RUN_NAME="$1"; shift
else
    RUN_NAME="${CONFIG_NAME}"
fi
EXTRA_ARGS="$@"

# Canonical run-name check (docs/naming.md). A canonical name also yields
# the dataset config: fields 3-6 = <algo>_<game>_<arm>_<opponent>.
if [[ "${RUN_NAME}" =~ ^([a-z0-9]+)_([0-9]+b)_(grpo|sdpo)_([a-z0-9]+)_([a-z0-9-]+)_([a-z0-9-]+)_([0-9]+|smoke[0-9]*)(_v[0-9]+)?$ ]]; then
    DATASET_DEFAULT="${BASH_REMATCH[3]}_${BASH_REMATCH[4]}_${BASH_REMATCH[5]}_${BASH_REMATCH[6]}"
else
    echo "WARNING: run name '${RUN_NAME}' is not canonical" >&2
    echo "  expected <model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>[_vN] (docs/naming.md)" >&2
    DATASET_DEFAULT="${CONFIG_NAME}"
fi

# ── 2. Cluster settings ──────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Account, checkpoint and store roots, source checkouts: scripts/slurm/cluster.env.
. "${REPO_ROOT}/scripts/slurm/cluster_env.sh" "${REPO_ROOT}"
ACCOUNT="${SLURM_ACCOUNT:?set SLURM_ACCOUNT in scripts/slurm/cluster.env}"
MORALGYM_DIR="${MORALGYM_DIR:-${REPO_ROOT}}"
SDPO_DIR="${SDPO_DIR:-${HOME}/SDPO}"
MORALGYM_CKPT_ROOT="${MORALGYM_CKPT_ROOT:-${SCRATCH}/moralgym_verl_runs}"
PARTITION="${PARTITION:-normal}"   # e.g. PARTITION=debug for smoke runs
TIME="${TIME:-12:00:00}"
# Host RAM. 460 GB suits <=9B; 32B needs more (see sdpo_run2_pd_qwen3_32b.yaml).
# GH200 nodes have 870 GB; the node is exclusive (4 GPUs), so ask for it.
MEM="${MEM:-460000}"
EDF_NAME="moralgym_verl"

# ── 3. Per-run dataset: paths ────────────────────────────────────────────────
# Regenerated every submit (step 4) so the parquet cannot go stale against the
# config — 2026-07-06 SDPO trained on a 4-day-old parquet with a superseded
# lambda/bias. On SCRATCH: no HOME quota, and the 30-day purge is the cleanup,
# since config + seed regenerate it bit-identically.
DATASET_SEED="${DATASET_SEED:-42}"
# DATASET_CONFIG decouples data from trainer, e.g. SDPO on the GRPO dataset.
# It also names the W&B group (step 8): with one reward-agnostic trainer
# config shared by several arms, the DATASET is the arm identity — which is
# why it is derived from the arm fields of the canonical run name (step 1).
DATASET_CONFIG="${DATASET_CONFIG:-${DATASET_DEFAULT}}"
DATASET_DIR="${SCRATCH}/moralgym_verl_datasets/${RUN_NAME}"

# ── 4. Per-run dataset: phase guard, then generate ───────────────────────────
# Turn structure lives in TWO files that must agree — dataset game.num_rounds
# and trainer multi_turn.enable — and a mismatch fails SILENTLY: multi-round
# data under a single-turn trainer generates one turn against a prompt written
# for five; 1-round data under the agent loop stops after round 1. Easy to hit
# via DATASET_CONFIG.
/usr/bin/python3.11 - "${REPO_ROOT}" "${DATASET_CONFIG}" "${CONFIG_NAME}" <<'PYEOF'
import sys, yaml
repo, ds_name, tr_name = sys.argv[1:4]
ds = yaml.safe_load(open(f"{repo}/configs/datasets/{ds_name}.yaml"))
tr = yaml.safe_load(open(f"{repo}/configs/training/{tr_name}.yaml"))
rounds = ds["game"]["num_rounds"]
mt = (tr.get("actor_rollout_ref", {}).get("rollout", {})
        .get("multi_turn", {}).get("enable", False))
if (rounds > 1) != bool(mt):
    sys.exit(
        f"ERROR: turn-structure mismatch — dataset {ds_name}.yaml has "
        f"num_rounds={rounds} but trainer {tr_name}.yaml has "
        f"multi_turn.enable={mt}.\n"
        f"  single-turn: num_rounds=1 + multi_turn.enable unset/false\n"
        f"  multi-turn:  num_rounds>1 + multi_turn.enable=true "
        f"(+ MoralGymRewardManager)"
    )
print(f"Phase check OK: num_rounds={rounds}, multi_turn.enable={bool(mt)}")
PYEOF

if [ -n "${DRY_RUN:-}" ]; then
    echo "DRY_RUN resolution:"
    echo "  config:  configs/training/${CONFIG_NAME}.yaml"
    echo "  run:     ${RUN_NAME}"
    echo "  dataset: configs/datasets/${DATASET_CONFIG}.yaml (seed ${DATASET_SEED})"
    echo "  ckpts:   ${MORALGYM_CKPT_ROOT}/${RUN_NAME}"
    echo "Stopping before dataset generation and sbatch."
    exit 0
fi

# System python3 on the login node is 3.6 and cannot parse this codebase.
echo "Generating dataset from configs/datasets/${DATASET_CONFIG}.yaml (seed ${DATASET_SEED})"
PYTHONPATH="${REPO_ROOT}/src" /usr/bin/python3.11 -m moralgym_verl.training.dataset \
    --config "${REPO_ROOT}/configs/datasets/${DATASET_CONFIG}.yaml" \
    --output-dir "${DATASET_DIR}" \
    --n-train 8000 --n-val 256 --seed "${DATASET_SEED}"

# ── 5. Slurm log destination ─────────────────────────────────────────────────
# ~/logs_verl splits by stage: pre_eval (base-model screens), training (here),
# eval (post-training checkpoints). HOME, so the SCRATCH purge cannot reach it.
OUTPUT_DIR="${HOME}/logs_verl/training"
mkdir -p "${OUTPUT_DIR}"

# ── 6. Container setup (runs inside the EDF container, before training) ──────
# Editable installs make repo edits live without rebuilding the image, and
# PYTHONPATH covers the src/ layout that --no-deps installs do not wire up.
SETUP_CMDS="pip install -e ${SDPO_DIR} --no-deps -q && \
pip install -e ${MORALGYM_DIR} --no-deps -q && \
export PYTHONPATH=${SDPO_DIR}:${MORALGYM_DIR}/src:\$PYTHONPATH"

# ── 7. Commands to run on the compute node ───────────────────────────────────
# Hydra: the PRIMARY config must resolve from --config-path — a searchpath
# entry alone does not (verified 2026-07-04):
#   <config_name>, _base_clariden → MoralGymVerl/configs/training/ (--config-path)
#   ppo_trainer                   → SDPO/verl/trainer/config/     (searchpath)
MORALGYM_CONFIG_PATH="${MORALGYM_DIR}/configs/training"
SDPO_CONFIG_PATH="${SDPO_DIR}/verl/trainer/config"
TRAIN_CMD="python -m verl.trainer.main_ppo \
  --config-path ${MORALGYM_CONFIG_PATH} \
  --config-name ${CONFIG_NAME} \
  'hydra.searchpath=[file://${SDPO_CONFIG_PATH}]' \
  trainer.experiment_name=${RUN_NAME} \
  ${EXTRA_ARGS}"

# Stage-out: checkpoints to MORALGYM_STORE_ROOT (long-term storage). Must run
# OUTSIDE the container: the store is not bind-mounted in the EDF, so an
# in-container write lands in the RAM overlay and vanishes at job end.
# && gates it on success; after a crash the checkpoints are still on the
# checkpoint root, cp -r by hand if worth keeping.
# CKPT_DIR matches vars.ckpt_dir in configs/training/_base_clariden.yaml
# (both read MORALGYM_CKPT_ROOT); rollouts/ rides along with the
# checkpoints since rollout_data_dir lives inside the run dir.
CKPT_DIR="${MORALGYM_CKPT_ROOT}/${RUN_NAME}"
if [ -n "${MORALGYM_STORE_ROOT:-}" ]; then
    STORE_CKPT="${MORALGYM_STORE_ROOT}/moralgym_verl/checkpoints/${RUN_NAME}"
    STAGEOUT_CMD="if [ -d ${CKPT_DIR} ]; then mkdir -p ${STORE_CKPT} && cp -r ${CKPT_DIR}/. ${STORE_CKPT}/ && echo Checkpoints staged to ${STORE_CKPT}; else echo No checkpoint dir at ${CKPT_DIR}, skipping stage-out; fi"
else
    STAGEOUT_CMD="echo MORALGYM_STORE_ROOT not set, checkpoints stay in ${CKPT_DIR}"
fi

# Host-memory sampler (scripts/slurm/mem_sampler.sh): runs outside the
# container for the life of the job and writes a .mem trace next to the log.
# sacct MaxRSS only sees the srun wrapper, so without this the host-RAM peak
# of a run is unobservable — which cost two blind smoke rounds on Qwen3-32B
# (2026-08-25). Cheap: one sleep loop, one line per 10s.
MEM_LOG="${OUTPUT_DIR}/\${SLURM_JOB_ID}_${RUN_NAME}.mem"
MEM_CMD="bash ${REPO_ROOT}/scripts/slurm/mem_sampler.sh 10 > ${MEM_LOG} 2>&1 & MEM_PID=\$!"

# --environment belongs on srun, not sbatch (sbatch --environment is experimental)
# Stage-out is gated on the training exit code; the sampler is always reaped
# and its peak echoed into the main log so the number is in one place.
WRAPPED_CMD="${MEM_CMD}; srun --environment=${EDF_NAME} bash -c '${SETUP_CMDS}; ${TRAIN_CMD}'; RC=\$?; kill \${MEM_PID} 2>/dev/null; echo \"[mem] peak: \$(tail -1 ${MEM_LOG})\"; if [ \${RC} -eq 0 ]; then ${STAGEOUT_CMD}; fi; exit \${RC}"

# ── 8. Submit ────────────────────────────────────────────────────────────────
echo "Submitting: ${RUN_NAME}"
echo "  config:  configs/training/${CONFIG_NAME}.yaml"
echo "  dataset: ${DATASET_DIR}"
echo ""

# One task owns the whole node: verl spawns its own Ray workers across the 4
# GPUs, so the usual --ntasks-per-node=4 convention does not apply. --export
# carries the run identity into the container, where _base_clariden.yaml reads
# it back via oc.env (MORALGYM_RUN_NAME/GROUP/DATASET_DIR).
sbatch \
    --job-name="mg-verl" \
    --account="${ACCOUNT}" \
    --nodes=1 \
    --partition="${PARTITION}" \
    --time="${TIME}" \
    --constraint="thp_never&nvidia_vboost_enabled" \
    --ntasks-per-node=1 \
    --gpus-per-node=4 \
    --mem="${MEM}" \
    --cpus-per-task=288 \
    --output="${OUTPUT_DIR}/%j_${RUN_NAME}.log" \
    --error="${OUTPUT_DIR}/%j_${RUN_NAME}.err" \
    --export="ALL,MORALGYM_RUN_NAME=${RUN_NAME},MORALGYM_GROUP=${DATASET_CONFIG},MORALGYM_DATASET_DIR=${DATASET_DIR},MORALGYM_DIR=${MORALGYM_DIR},SDPO_DIR=${SDPO_DIR},MORALGYM_CKPT_ROOT=${MORALGYM_CKPT_ROOT},WANDB_API_KEY" \
    --wrap="${WRAPPED_CMD}"
