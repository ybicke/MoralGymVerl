#!/bin/bash
#SBATCH --job-name=teacher-signal
#SBATCH --account=aa004
#SBATCH --partition=normal
#SBATCH --output=/dev/null
#SBATCH --error=/dev/null
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH -C thp_never&nvidia_vboost_enabled
#SBATCH --time=02:00:00

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
# Stage 1a sweep (single fabricated-history round, per-state policy):
#   for mv in none no_exploit_forgive deon_no_exploit consequentialist; do
#     sbatch scripts/slurm/eval_teacher_signal.sh prisoners_dilemma $mv 200 \
#         --num-rounds 1 --game-design hist --opponent random
#   done
# Stage 1b (multi-turn dynamics, config defaults): drop the extra flags.
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

# Redirect output early so errors are never lost to /dev/null
LOG_BASE="${HOME}/logs"
mkdir -p "${LOG_BASE}/slurm"
RUN_NAME="teacher_signal_${GAME}_${MORAL_VALUE}_${SLURM_JOB_ID}"
exec > "${LOG_BASE}/slurm/${RUN_NAME}.out" 2> "${LOG_BASE}/slurm/${RUN_NAME}.err"

CONFIG="configs/eval/teacher_signal_9b.yaml"
# Results layout: eval_results/teacher_signal/<EVAL_GROUP>/<cell>/
#   EVAL_GROUP names the experiment stage (stage1_single_round,
#   stage1b_multiturn, robustness, smoke, ...; default: adhoc). Set at
#   submit time:  EVAL_GROUP=robustness sbatch ...
# One directory per run cell; filenames inside say what they contain:
#   behavioral.json / behavioral.responses.jsonl
#   logprob_a.json / logprob_b.json / logprob_b.traces.jsonl
EVAL_GROUP="${EVAL_GROUP:-adhoc}"
RUN_DIR="${PROJECT_ROOT}/eval_results/teacher_signal/${EVAL_GROUP}/${GAME}__${MORAL_VALUE}_${SLURM_JOB_ID}"
OUTPUT="${RUN_DIR}/behavioral.json"
mkdir -p "${RUN_DIR}"

echo "============================================="
echo "Teacher-signal eval (Session 1)"
echo "Job ID:       ${SLURM_JOB_ID}"
echo "Node:         ${SLURM_NODELIST}"
echo "Game:         ${GAME}"
echo "Moral value:  ${MORAL_VALUE}"
echo "Episodes:     ${NUM_EPISODES}"
echo "Output:       ${OUTPUT}"
echo "Started:      $(date)"
echo "============================================="

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
        --save-raw-responses \
        --output "${OUTPUT}" \
        "${@:4}"

echo "Behavioral eval complete: $(date)"

# Logprob probes A+B (teacher-vs-student forward passes; see
# eval/logprob_probe.py). Skipped for the plain baseline — they compare
# against the plain prompt internally. Same job, same GPU, minutes.
# RUN_PROBES=off skips them (e.g. robustness / multi-turn cells, where the
# single-round fixed-presentation probes add no information).
if [ "${MORAL_VALUE}" != "none" ] && [ "${RUN_PROBES:-on}" != "off" ]; then
    srun --environment=moralgym_verl \
        --gpus-per-task=1 \
        python3 -m moralgym_verl.eval.logprob_probe \
            --config "${PROJECT_ROOT}/${CONFIG}" \
            --checkpoint base \
            --game "${GAME}" \
            --moral-value "${MORAL_VALUE}" \
            --output-dir "${RUN_DIR}"
    echo "Probes complete: $(date)"
fi

# Multi-turn signal-decay probe (RUN_MT_PROBE=on; see
# eval/logprob_probe_multiturn.py): per-round teacher-vs-student deltas
# with the moral value at episode start (training-exact). ~15 min.
if [ "${MORAL_VALUE}" != "none" ] && [ "${RUN_MT_PROBE:-off}" = "on" ]; then
    srun --environment=moralgym_verl \
        --gpus-per-task=1 \
        python3 -m moralgym_verl.eval.logprob_probe_multiturn \
            --config "${PROJECT_ROOT}/${CONFIG}" \
            --checkpoint base \
            --game "${GAME}" \
            --moral-value "${MORAL_VALUE}" \
            --output-dir "${RUN_DIR}"
    echo "Multi-turn probe complete: $(date)"
fi

# Stage out the whole run directory to $STORE (tape-backed) for durability.
if [ -n "${STORE_BASE:-}" ] && [ -d "${RUN_DIR}" ]; then
    STORE_EVAL_DIR="${STORE_BASE}/eval_results/teacher_signal/${EVAL_GROUP}"
    mkdir -p "${STORE_EVAL_DIR}"
    cp -r "${RUN_DIR}" "${STORE_EVAL_DIR}/"
    echo "Backed up run dir: ${EVAL_GROUP}/$(basename "${RUN_DIR}")"
fi
