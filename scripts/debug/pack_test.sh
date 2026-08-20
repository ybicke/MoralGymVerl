#!/bin/bash
# =============================================================================
# GPU-packing feasibility test.
#
# Clariden's partitions are OverSubscribe=EXCLUSIVE, so a 1-GPU job still
# occupies a whole 4-GPU node — 3 GPUs idle per eval cell. The only way to
# reclaim them is to run several cells as concurrent job STEPS inside one
# allocation. This checks whether that actually works with EDF containers.
#
# Three questions, all answered by the output:
#   1. CONCURRENCY — do the 4 steps overlap, or serialize? Each sleeps 30s;
#      concurrent means all 4 start within a few seconds and all end ~30s
#      later. Serialized means end times staggered by ~30s.
#   2. DISTINCT GPUs — each step prints its GPU UUID. Four different UUIDs
#      means Slurm bound them to separate devices; a repeat means they are
#      sharing one, which would halve throughput and risk OOM.
#   3. CONTAINER COMPAT — whether `--environment=` tolerates `--exact`
#      steps sharing an allocation. This is the real unknown.
#
# Run (from the repo root):
#   sbatch scripts/debug/pack_test.sh
#   cat ~/logs_verl/pre_eval/pack_test_<jobid>.out
# =============================================================================
#SBATCH --job-name=pack-test
#SBATCH --account=aa004
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=4
#SBATCH --gpus-per-node=4
#SBATCH -C thp_never&nvidia_vboost_enabled
#SBATCH --time=00:15:00
#SBATCH --output=/dev/null
#SBATCH --error=/dev/null

set -uo pipefail

LOG_BASE="${HOME}/logs_verl/pre_eval"
mkdir -p "${LOG_BASE}"
exec > "${LOG_BASE}/pack_test_${SLURM_JOB_ID}.out" 2>&1

echo "=== allocation ==="
echo "job ${SLURM_JOB_ID} on ${SLURM_NODELIST}"
echo "tasks=${SLURM_NTASKS} gpus_on_node=$(nvidia-smi -L | wc -l)"
echo "wall-clock start: $(date +%T)"
echo

pids=()
for i in 0 1 2 3; do
    # --exact is essential: without it the first step claims every CPU/GPU
    # in the allocation and the rest queue behind it.
    srun --exact --ntasks=1 --gpus-per-task=1 --cpus-per-task=64 \
         --environment=moralgym_verl bash -c "
            echo \"[step $i] START \$(date +%T) uuid=\$(nvidia-smi --query-gpu=uuid --format=csv,noheader | head -1)\"
            python3 -c \"
import torch, time
assert torch.cuda.is_available(), 'no CUDA'
x = torch.randn(4096, 4096, device='cuda')
for _ in range(20): x = x @ x.T / 1e4    # keep the GPU actually busy
torch.cuda.synchronize()
time.sleep(30)
print('[step $i] torch ok, visible devices:', torch.cuda.device_count())
\"
            echo \"[step $i] END   \$(date +%T)\"
         " &
    pids+=($!)
done

# Per-step exit codes: a silent failure inside `wait` is exactly the trap a
# real packed launcher has to avoid.
fail=0
for idx in "${!pids[@]}"; do
    if ! wait "${pids[$idx]}"; then
        echo "[step ${idx}] FAILED"
        fail=1
    fi
done

echo
echo "wall-clock end: $(date +%T)"
echo "=== verdict ==="
echo "PACKING USABLE if: 4 distinct uuids, all STARTs within ~10s,"
echo "all ENDs within ~10s of each other, and no FAILED lines."
echo "overall exit: ${fail}"
exit "${fail}"
