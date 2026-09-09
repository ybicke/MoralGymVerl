#!/bin/bash
# =============================================================================
# Setup script for MoralGymVerl on Clariden (GH200 / aarch64)
#
# Builds the container from SDPO's Dockerfile.gh200, exports it as a sqsh
# file for EDF, writes the EDF config, and creates output directories.
#
# Prerequisites:
#   - podman available on the login node (or run inside an interactive job)
#   - SDPO repo at ~/SDPO
#   - MoralGymVerl repo at ~/MoralGymVerl
#   - $SCRATCH is set
#
# Usage:
#   bash ~/MoralGymVerl/scripts/setup/setup_verl.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SDPO_DIR="${HOME}/SDPO"
SCRATCH="${SCRATCH:-/iopsstor/scratch/cscs/$USER}"
CONTAINER_DIR="${SCRATCH}/containers"
SQSH="${CONTAINER_DIR}/moralgym-verl-gh200.sqsh"
EDF_NAME="moralgym_verl"
EDF_FILE="${HOME}/.edf/${EDF_NAME}.toml"

echo "=== MoralGymVerl setup for Clariden ==="
echo "  Repo:       ${REPO_ROOT}"
echo "  SDPO:       ${SDPO_DIR}"
echo "  Container:  ${SQSH}"
echo ""

# ── Container build ──────────────────────────────────────────────────────────
if [ -f "${SQSH}" ]; then
    echo "[skip] Container already exists: ${SQSH}"
    echo "       Delete it and rerun to rebuild."
else
    echo "[1/3] Building container image..."
    # Build from MoralGymVerl root; Dockerfile.gh200 COPYs SDPO from ../SDPO
    # We create a temporary build context that includes both repos.
    BUILD_CTX=$(mktemp -d)
    cp -r "${REPO_ROOT}/." "${BUILD_CTX}/"
    cp -r "${SDPO_DIR}" "${BUILD_CTX}/SDPO"
    cp "${SDPO_DIR}/requirements-gh200.txt" "${BUILD_CTX}/requirements-gh200.txt"

    podman build "${BUILD_CTX}" -f "${REPO_ROOT}/Dockerfile.gh200" \
        -t moralgym-verl-gh200:latest

    rm -rf "${BUILD_CTX}"

    echo "[2/3] Exporting to sqsh (this may take several minutes)..."
    mkdir -p "${CONTAINER_DIR}"
    enroot import -x mount -o "${SQSH}" podman://localhost/moralgym-verl-gh200:latest
    echo "      Written: ${SQSH}"
fi

# ── EDF config ───────────────────────────────────────────────────────────────
echo "[3/3] Writing EDF config: ${EDF_FILE}"
mkdir -p "${HOME}/.edf"
cat > "${EDF_FILE}" << TOML
# EDF config for MoralGymVerl (verl + SDPO on GH200)
# Selector: srun --environment=${EDF_NAME} <command>
image = "${SQSH}"
workdir = "/users/${USER}"

mounts = [
    "/users/${USER}:/users/${USER}",
    "${SCRATCH}:${SCRATCH}",
]

[env]
# moralgym_verl and SDPO are reinstalled at job start from bind-mounted source
# (see train_verl.sh) so code changes take effect without rebuilding the image.
PYTHONPATH = "/users/${USER}/MoralGymVerl/src:/users/${USER}/SDPO"
HF_HOME = "${SCRATCH}/MoralGym_Storage/.cache/huggingface"
HF_HUB_OFFLINE = "1"
TOKENIZERS_PARALLELISM = "false"
# Gemma-2 tanh softcapping requires FlashInfer attention backend
VLLM_ATTENTION_BACKEND = "FLASHINFER"
# WANDB_API_KEY intentionally NOT set here: EDF [env] overrides the job
# environment, so an empty value would clobber the key exported by sbatch
# (train_verl.sh --export). It passes through from the host env instead.

# Single-node training uses NVLink; for multi-node uncomment:
# [annotations]
# com.hooks.aws_ofi_nccl.enabled = "true"
TOML

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Download model (if not already in HF cache):"
echo "     bash ~/MoralGym/scripts/setup/download_hf_model.sh google/gemma-2-9b-it"
echo ""
echo "  2. Check a run resolves (dataset is generated automatically at"
echo "     submit time into \$SCRATCH/moralgym_verl_datasets/<run>):"
echo "     DRY_RUN=1 bash ~/MoralGymVerl/scripts/slurm/train_verl.sh <run_name>"
echo ""
echo "  3. Submit a training job (docs/naming.md: config name = run name):"
echo "     bash ~/MoralGymVerl/scripts/slurm/train_verl.sh qwen3_8b_grpo_pd_util_tft_150"
