#!/bin/bash
# =============================================================================
# Submit a MoralGymVerl GRPO or SDPO training job on Clariden.
#
# Usage:
#   bash train_verl.sh <config_name> <run_name> [extra verl overrides...]
#
# Examples:
#   bash train_verl.sh grpo_pd_tft grpo_pd_tft_seed1
#   bash train_verl.sh sdpo_pd_tft sdpo_pd_tft_s1 actor_rollout_ref.actor.optim.lr=1e-5
#
# Config names map to configs/verl/<name>.yaml (no .yaml suffix).
# Run name sets the W&B experiment name and checkpoint directory.
# =============================================================================
set -euo pipefail

CONFIG_NAME="${1:-grpo_pd_tft}"
RUN_NAME="${2:-${CONFIG_NAME}_$(date +%Y%m%d_%H%M%S)}"
shift 2 || true          # remaining args passed to verl as overrides
EXTRA_ARGS="$@"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCOUNT="aa004"
PARTITION="${PARTITION:-normal}"   # e.g. PARTITION=debug for smoke runs
TIME="${TIME:-12:00:00}"
EDF_NAME="moralgym_verl"

# Each config's dataset must already exist (generate_dataset.sh).
# We export the dataset name via env var so user.yaml can resolve the path.
DATASET_NAME="${CONFIG_NAME}"

OUTPUT_DIR="${HOME}/output/MoralGymVerl"
mkdir -p "${OUTPUT_DIR}"

# ── Setup commands run inside the container at job start ─────────────────────
# Reinstall from source so code changes are live without image rebuilds.
SETUP_CMDS="pip install -e /users/${USER}/SDPO --no-deps -q && \
pip install -e /users/${USER}/MoralGymVerl --no-deps -q && \
export PYTHONPATH=/users/${USER}/SDPO:/users/${USER}/MoralGymVerl/src:\$PYTHONPATH"

# ── verl training command ────────────────────────────────────────────────────
# Hydra: the PRIMARY config must live in --config-path (searchpath alone does
# not resolve it — verified 2026-07-04). So --config-path points at our dir
# and SDPO's verl/trainer/config/ goes on the searchpath for the ppo_trainer
# default. Resolution order when loading defaults:
#   grpo_pd_tft   → MoralGymVerl/configs/verl/ (primary, --config-path)
#   ppo_trainer   → SDPO's verl/trainer/config/ (searchpath)
#   moralgym_user → MoralGymVerl/configs/verl/ (--config-path)
MORALGYM_CONFIG_PATH="/users/${USER}/MoralGymVerl/configs/verl"
SDPO_CONFIG_PATH="/users/${USER}/SDPO/verl/trainer/config"
TRAIN_CMD="python -m verl.trainer.main_ppo \
  --config-path ${MORALGYM_CONFIG_PATH} \
  --config-name ${CONFIG_NAME} \
  'hydra.searchpath=[file://${SDPO_CONFIG_PATH}]' \
  trainer.experiment_name=${RUN_NAME} \
  ${EXTRA_ARGS}"

# --environment belongs on srun, not sbatch (sbatch --environment is experimental)
WRAPPED_CMD="srun --environment=${EDF_NAME} bash -c '${SETUP_CMDS}; ${TRAIN_CMD}'"

echo "Submitting: ${RUN_NAME}"
echo "  config:  configs/verl/${CONFIG_NAME}.yaml"
echo "  dataset: ${DATASET_NAME}"
echo ""

sbatch \
    --job-name="mg-verl" \
    --account="${ACCOUNT}" \
    --nodes=1 \
    --partition="${PARTITION}" \
    --time="${TIME}" \
    --constraint="thp_never&nvidia_vboost_enabled" \
    --ntasks-per-node=1 \
    --gpus-per-node=4 \
    --mem=460000 \
    --cpus-per-task=288 \
    --output="${OUTPUT_DIR}/%j_${RUN_NAME}.log" \
    --error="${OUTPUT_DIR}/%j_${RUN_NAME}.err" \
    --export="ALL,MORALGYM_RUN_NAME=${RUN_NAME},MORALGYM_GROUP=${CONFIG_NAME},MORALGYM_DATASET=${DATASET_NAME},WANDB_API_KEY" \
    --wrap="${WRAPPED_CMD}"
