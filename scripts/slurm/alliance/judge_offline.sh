#!/bin/bash
#SBATCH --job-name=mg-judge-offline
#SBATCH --time=03:00:00
#SBATCH --gpus-per-node=h100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=100G
#SBATCH --output=%x_%j.out
# =============================================================================
# Offline comparison of the judge variants (scripts/analysis/judge_offline.py)
# on one H100: starts a vLLM judge server inside the container, waits until it
# answers, scores saved traces with every variant, stops the server.
# Design: ~/moralgym-report/notes/judge_grpo_design.tex (check 1).
#
# Run from the repo root (Fir or Rorqual; the data file must be on the cluster):
#   sbatch scripts/slurm/alliance/judge_offline.sh <traces.json> [extra judge_offline.py args]
# e.g. sbatch scripts/slurm/alliance/judge_offline.sh \
#        $SCRATCH/judge_offline/qwen3_8b__pd_single_round.json --per-prompt 16
# Results: $SCRATCH/judge_offline/<job id>/{summary.md,summary.json,scores.jsonl}
#
# The server runs WITHOUT a vLLM reasoning parser, so thinking tokens stay in
# the returned log-probs (judge.py reads the answer after </think>). Context
# 16384 = judge prompt (~1.5k) + response (<=2k) + thinking budget (8192) + slack.
# =============================================================================
set -euo pipefail
PROJECT_ROOT="${SLURM_SUBMIT_DIR}"
[ -f "${PROJECT_ROOT}/pyproject.toml" ] || { echo "ERROR: submit from the MoralGymVerl repo root." >&2; exit 1; }
. "${PROJECT_ROOT}/scripts/slurm/alliance/fir_env.sh" "${PROJECT_ROOT}"

DATA="${1:?Usage: judge_offline.sh <traces.json> [judge_offline.py args]}"
shift
JUDGE_MODEL="${JUDGE_MODEL:-Qwen/Qwen3-8B}"
PORT="${JUDGE_PORT:-8000}"
OUT="${SCRATCH}/judge_offline/${SLURM_JOB_ID}"
mkdir -p "${OUT}"
export PYTHONPATH="${SDPO_DIR:-${HOME}/SDPO}:${PROJECT_ROOT}/src"
echo "judge offline: job ${SLURM_JOB_ID} on ${SLURM_NODELIST} (${MORALGYM_CLUSTER}), model ${JUDGE_MODEL}, data ${DATA}, out ${OUT}"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# shellcheck disable=SC2086
srun ${FIR_CONTAINER_EXEC} bash -c "
set -euo pipefail
python -m vllm.entrypoints.openai.api_server \
    --model '${JUDGE_MODEL}' --served-model-name '${JUDGE_MODEL}' \
    --port ${PORT} --max-model-len 16384 --enable-prefix-caching \
    --gpu-memory-utilization 0.9 \
    > '${OUT}/vllm_server.log' 2>&1 &
SERVER=\$!
trap 'kill \${SERVER} 2>/dev/null || true' EXIT
for i in \$(seq 1 120); do
    curl -sf http://localhost:${PORT}/health >/dev/null && break
    kill -0 \${SERVER} 2>/dev/null || { echo 'judge server died, see ${OUT}/vllm_server.log'; exit 1; }
    sleep 5
done
curl -sf http://localhost:${PORT}/health >/dev/null || { echo 'judge server not up after 10 min'; exit 1; }
echo \"judge server up after \$((i * 5)) s\"
python '${PROJECT_ROOT}/scripts/analysis/judge_offline.py' --data '${DATA}' \
    --endpoint http://localhost:${PORT}/v1 --model '${JUDGE_MODEL}' --out '${OUT}' $*
"
echo "done: ${OUT}/summary.md"
cat "${OUT}/summary.md"
