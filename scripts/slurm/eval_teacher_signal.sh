#!/bin/bash
#SBATCH --job-name=teacher-signal
#SBATCH --account=aa004
#SBATCH --partition=normal
#SBATCH --output=/dev/null
#SBATCH --error=/dev/null
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH -C thp_never&nvidia_vboost_enabled
#SBATCH --time=03:00:00

# =============================================================================
# Session 1 — Teacher-signal eval (one game x moral-value cell)
#
# Evaluates BASE gemma-2-9b-it with a moral value wrapped around the game
# prompt via the SDPO reprompt_template (see eval/teacher_context.py).
# HF-transformers inference, 1 GPU, no Ray/vLLM.
#
# Usage (from the MoralGymVerl repo root):
#   sbatch scripts/slurm/eval_teacher_signal.sh <game> <moral_value> [num_episodes]
#   sbatch scripts/slurm/eval_teacher_signal.sh prisoners_dilemma deon_no_exploit
#
# single_round sweep (single fabricated-history round, per-state policy):
#   for mv in none no_exploit_forgive deon_no_exploit consequentialist; do
#     sbatch scripts/slurm/eval_teacher_signal.sh prisoners_dilemma $mv 200 \
#         --protocol single_round
#   done
# multi_round (multi-round dynamics, config defaults): drop the extra flags.
#
# Env toggles: EVAL_GROUP=<dir> (results subdir), RUN_PROBES=off,
# RUN_PROBE_B_EPISODE=on, MODEL=<hf id> (overrides the config's
# policy.model_name), PROTOCOL=single_round|multi_round (preset applied to
# behavioral AND the probes), REPRESENTATION=matrix|prose|list (payoff-block
# rendering, forwarded to behavioral + all probes; default matrix),
#
# MODEL/PROTOCOL/REPRESENTATION travel by env rather than as trailing flags
# because trailing flags ("${@:4}") reach behavioral only. Anything that
# defines what the cell IS must also reach the probes, or one cell reports
# behavioral and probe numbers for two different settings.
# PROBE_TEMPERATURE=<T> (probe-B trace sampling; config
# default 0.7 = training parity, pass 1.0 for July-comparable runs —
# behavioral temperature is a separate --temperature forwarded arg).
# =============================================================================

set -euo pipefail

export PROJECT_ROOT="${SLURM_SUBMIT_DIR}"
if [ ! -f "${PROJECT_ROOT}/pyproject.toml" ]; then
    echo "ERROR: sbatch must be run from the MoralGymVerl repo root." >&2
    exit 1
fi
export STORE_BASE="/capstor/store/cscs/swissai/aa004/${USER}"

GAME="${1:?Usage: eval_teacher_signal.sh <game> <moral_value> [num_episodes]}"
MORAL_VALUE="${2:?Usage: eval_teacher_signal.sh <game> <moral_value> [num_episodes]}"
NUM_EPISODES="${3:-25}"

# Redirect output early so errors are never lost to /dev/null.
# Logs: ~/logs_verl/{pre_eval,training,eval}. Base-model screens (the
# default) go to pre_eval; submit with EVAL_STAGE=eval for post-training
# checkpoint evals.
LOG_BASE="${HOME}/logs_verl/${EVAL_STAGE:-pre_eval}"
mkdir -p "${LOG_BASE}"
RUN_NAME="teacher_signal_${GAME}_${MORAL_VALUE}_${SLURM_JOB_ID}"
exec > "${LOG_BASE}/${RUN_NAME}.out" 2> "${LOG_BASE}/${RUN_NAME}.err"

CONFIG="${CONFIG:-configs/eval/teacher_signal_9b.yaml}"
# Results layout: eval_results/teacher_signal/<EVAL_GROUP>/<cell>/
#   EVAL_GROUP names the experiment campaign (single_round, multi_round,
#   robustness, smoke, ...; default: adhoc). Set at
#   submit time:  EVAL_GROUP=robustness sbatch ...
# One directory per run cell; filenames inside say what they contain:
#   behavioral.json / behavioral.responses.jsonl
#   probe_a.json / probe_b.json / probe_b.traces.jsonl
EVAL_GROUP="${EVAL_GROUP:-adhoc}"

# Phase guard. The episode mode of probe B is the multi-round teacher-decay
# probe; under a single-round preset it has no curve to measure. Fail at job
# start rather than emit a one-round 'decay' file into a single-turn group.
if [ "${PROTOCOL:-}" = "single_round" ] && [ "${RUN_PROBE_B_EPISODE:-off}" = "on" ]; then
    echo "ERROR: PROTOCOL=single_round with RUN_PROBE_B_EPISODE=on — the" >&2
    echo "       episode probe is multi-round only. Unset one of them." >&2
    exit 1
fi
RUN_DIR="${PROJECT_ROOT}/eval_results/teacher_signal/${EVAL_GROUP}/cells/${GAME}__${MORAL_VALUE}_${SLURM_JOB_ID}"
OUTPUT="${RUN_DIR}/behavioral.json"
mkdir -p "${RUN_DIR}"

echo "============================================="
echo "Teacher-signal eval (Session 1)"
echo "Job ID:       ${SLURM_JOB_ID}"
echo "Node:         ${SLURM_NODELIST}"
echo "Game:         ${GAME}"
echo "Moral value:  ${MORAL_VALUE}"
echo "Model:        ${MODEL:-from config}"
echo "Protocol:     ${PROTOCOL:-from config}"
echo "Representation: ${REPRESENTATION:-matrix (default)}"
echo "Episodes:     ${NUM_EPISODES}"
echo "Output:       ${OUTPUT}"
echo "Started:      $(date)"
echo "============================================="

# Env fingerprint — record the numerics-relevant stack per run (driver,
# CUDA libs, kernel) so cross-job divergence can be checked against env
# drift, not just guessed at.
echo "--- env fingerprint ---"
echo "TORCH_DETERMINISTIC=${TORCH_DETERMINISTIC:-unset} CUBLAS_WORKSPACE_CONFIG=${CUBLAS_WORKSPACE_CONFIG:-unset}"
uname -r
nvidia-smi --query-gpu=name,driver_version,clocks.sm,clocks.mem --format=csv
srun --environment=moralgym_verl --gpus-per-task=1 python3 -c "
import torch, transformers
print('torch', torch.__version__, '| cuda', torch.version.cuda, '| cudnn', torch.backends.cudnn.version())
print('transformers', transformers.__version__)
print('tf32 matmul', torch.backends.cuda.matmul.allow_tf32, '| tf32 cudnn', torch.backends.cudnn.allow_tf32)
print('deterministic_algorithms', torch.are_deterministic_algorithms_enabled())
"
echo "--- end fingerprint ---"

# --save-raw-responses: keep every (wrapped prompt, reasoning trace) pair —
# reading whether the model actually invokes the moral value is half the
# point of the screening. Extra args after the 3 positionals are forwarded.
srun --environment=moralgym_verl \
    --gpus-per-task=1 \
    python3 -m moralgym_verl.eval.behavioral \
        --config "${PROJECT_ROOT}/${CONFIG}" \
        --checkpoint base \
        --game "${GAME}" \
        --moral-value "${MORAL_VALUE}" \
        --num-episodes "${NUM_EPISODES}" \
        ${MODEL:+--model "${MODEL}"} \
        ${PROTOCOL:+--protocol "${PROTOCOL}"} \
        ${REPRESENTATION:+--representation "${REPRESENTATION}"} \
        --save-raw-responses \
        --output "${OUTPUT}" \
        "${@:4}"

echo "Behavioral eval complete: $(date)"

# Probes (teacher-vs-student forward passes): probe A =
# eval/probe_a.py, probe B = eval/probe_b.py.
# Skipped for the plain baseline — they compare against the plain prompt
# internally. Same job, same GPU, minutes. RUN_PROBES=off skips them
# (e.g. robustness / multi-turn cells, where the single-round
# fixed-presentation probes add no information).
if [ "${MORAL_VALUE}" != "none" ] && [ "${RUN_PROBES:-on}" != "off" ]; then
    srun --environment=moralgym_verl \
        --gpus-per-task=1 \
        python3 -m moralgym_verl.eval.probe_a \
            --config "${PROJECT_ROOT}/${CONFIG}" \
            --checkpoint base \
            --game "${GAME}" \
            --moral-value "${MORAL_VALUE}" \
            ${MODEL:+--model "${MODEL}"} \
            ${PROTOCOL:+--protocol "${PROTOCOL}"} \
            ${REPRESENTATION:+--representation "${REPRESENTATION}"} \
            --output-dir "${RUN_DIR}"
    srun --environment=moralgym_verl \
        --gpus-per-task=1 \
        python3 -m moralgym_verl.eval.probe_b \
            --config "${PROJECT_ROOT}/${CONFIG}" \
            --checkpoint base \
            --game "${GAME}" \
            --moral-value "${MORAL_VALUE}" \
            --states fabricated \
            ${MODEL:+--model "${MODEL}"} \
            ${PROTOCOL:+--protocol "${PROTOCOL}"} \
            ${REPRESENTATION:+--representation "${REPRESENTATION}"} \
            ${PROBE_TEMPERATURE:+--temperature "${PROBE_TEMPERATURE}"} \
            --output-dir "${RUN_DIR}"
    echo "Probes complete: $(date)"
fi

# Probe B episode mode (RUN_PROBE_B_EPISODE=on): probe B over live
# episodes — per-round teacher-vs-student deltas with the moral value at
# episode start (training-exact). ~15 min.
if [ "${MORAL_VALUE}" != "none" ] && [ "${RUN_PROBE_B_EPISODE:-off}" = "on" ]; then
    srun --environment=moralgym_verl \
        --gpus-per-task=1 \
        python3 -m moralgym_verl.eval.probe_b \
            --config "${PROJECT_ROOT}/${CONFIG}" \
            --checkpoint base \
            --game "${GAME}" \
            --moral-value "${MORAL_VALUE}" \
            --states episode \
            ${MODEL:+--model "${MODEL}"} \
            ${PROTOCOL:+--protocol "${PROTOCOL}"} \
            ${REPRESENTATION:+--representation "${REPRESENTATION}"} \
            ${PROBE_TEMPERATURE:+--temperature "${PROBE_TEMPERATURE}"} \
            --output-dir "${RUN_DIR}"
    echo "Probe B episode mode complete: $(date)"
fi

# Stage out the whole run directory to $STORE (tape-backed) for durability.
if [ -n "${STORE_BASE:-}" ] && [ -d "${RUN_DIR}" ]; then
    STORE_EVAL_DIR="${STORE_BASE}/eval_results/teacher_signal/${EVAL_GROUP}"
    mkdir -p "${STORE_EVAL_DIR}"
    cp -r "${RUN_DIR}" "${STORE_EVAL_DIR}/"
    echo "Backed up run dir: ${EVAL_GROUP}/$(basename "${RUN_DIR}")"
fi
