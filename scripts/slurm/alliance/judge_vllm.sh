#!/bin/bash
# =============================================================================
# The LLM judge server for training (training/judge.py), run INSIDE the
# container on the judge component of a JUDGE=1 job (train_job.sh starts it
# with `srun --het-group=1`). Serves an OpenAI-compatible vLLM endpoint until
# the job step is killed.
#   judge_vllm.sh <model> <port>
#
# Frozen base model, the whole GPU. No vLLM reasoning parser, so thinking
# tokens stay in the returned log-probs (judge.py reads the answer after
# </think>). Context 16384 = judge prompt (~1.5k) + rollout (<=2k) +
# thinking budget (8192) + slack. Prefix caching: the game prompt and the
# principle are shared by the 16 rollouts of a prompt.
# =============================================================================
set -euo pipefail
MODEL="${1:?Usage: judge_vllm.sh <model> <port>}"
PORT="${2:?Usage: judge_vllm.sh <model> <port>}"
echo "[judge] $(hostname) port ${PORT} model ${MODEL} ($(python -c 'import vllm; print("vllm", vllm.__version__)'))"
exec python -m vllm.entrypoints.openai.api_server \
    --model "${MODEL}" --served-model-name "${MODEL}" \
    --host 0.0.0.0 --port "${PORT}" \
    --max-model-len 16384 --enable-prefix-caching \
    --gpu-memory-utilization 0.9
