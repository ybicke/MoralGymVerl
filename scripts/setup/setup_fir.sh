#!/bin/bash
#SBATCH --job-name=mg-setup-fir
#SBATCH --time=03:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --output=%x_%j.out
# =============================================================================
# Setup for MoralGymVerl on Fir (Alliance Canada, x86-64 H100, Apptainer).
# The Fir counterpart of scripts/setup/setup_verl.sh (Clariden, untouched).
#
# 1. SDPO checkout at the pinned commit + patches (scripts/setup/checkout_sdpo.sh)
# 2. Host virtualenv for the submit-time scripts (dataset generation, sweep
#    expansion, tests, analysis): $FIR_VENV  (pyarrow comes from `module load arrow`)
# 3. Container image from Apptainer.fir.def: $FIR_SIF
# 4. Output directories on $SCRATCH
#
# The image build pulls ~20 GB and converts it to SIF, too heavy for the
# login node's per-user cgroup (6 cores / 16 GiB), so submit it as a CPU
# job; compute nodes have internet. The account must have a CPU allocation
# and must be given on the command line (~/.bashrc exports rrg-zhijing,
# which is GPU-only, and env vars override #SBATCH lines):
#   sbatch --account=def-zhijing scripts/setup/setup_fir.sh
# Steps 1, 2 and 4 are cheap and also run as `bash scripts/setup/setup_fir.sh`
# on the login node once the image exists.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "${REPO_ROOT}/scripts/slurm/fir/fir_env.sh" "${REPO_ROOT}"
SDPO_DIR="${SDPO_DIR:-${HOME}/SDPO}"
SCRATCH="${SCRATCH:?SCRATCH must be set}"

echo "=== MoralGymVerl setup for Fir ==="
echo "  Repo:       ${REPO_ROOT}"
echo "  SDPO:       ${SDPO_DIR}"
echo "  venv:       ${FIR_VENV}"
echo "  Container:  ${FIR_SIF}"
echo "  HF_HOME:    ${HF_HOME}"
echo "  Node:       $(hostname)  job: ${SLURM_JOB_ID:-none (login node)}"
echo ""

# ── 1. SDPO ──────────────────────────────────────────────────────────────────
echo "[1/4] SDPO checkout"
bash "${REPO_ROOT}/scripts/setup/checkout_sdpo.sh" "${SDPO_DIR}"

# ── 2. Host virtualenv ───────────────────────────────────────────────────────
# Alliance convention: module python + virtualenv --no-download + the
# cluster's wheelhouse (--no-index). In $HOME, not $SCRATCH (purge).
# pyarrow is NOT pip-installable here (the wheelhouse serves a dummy that
# says to load the arrow module), so FIR_MODULES carries arrow/25.0.0.
echo "[2/4] Host virtualenv: ${FIR_VENV}"
if [ ! -x "${FIR_VENV}/bin/python" ]; then
    virtualenv --no-download "${FIR_VENV}"
fi
"${FIR_VENV}/bin/pip" install --no-index --upgrade pip -q
"${FIR_VENV}/bin/pip" install --no-index -q pyyaml numpy pandas datasets matplotlib scipy pytest huggingface_hub
"${FIR_VENV}/bin/python" -c "import yaml, pandas, pyarrow, numpy, pytest; print('      venv ok, python', __import__('sys').version.split()[0], 'pyarrow', pyarrow.__version__)"

# ── 3. Container image ───────────────────────────────────────────────────────
echo "[3/4] Container image"
if [ -f "${FIR_SIF}" ]; then
    echo "[skip] already exists: ${FIR_SIF}"
    echo "       Delete it and rerun to rebuild."
else
    mkdir -p "$(dirname "${FIR_SIF}")"
    # Caches on scratch / node-local disk: the pull is ~20 GB of layers.
    export APPTAINER_CACHEDIR="${SCRATCH}/.apptainer/cache"
    export APPTAINER_TMPDIR="${SLURM_TMPDIR:-${SCRATCH}/.apptainer/tmp}"
    mkdir -p "${APPTAINER_CACHEDIR}" "${APPTAINER_TMPDIR}"
    cd "${REPO_ROOT}"   # %files paths in the .def are relative to here
    echo "      building ${FIR_SIF} from Apptainer.fir.def (this takes a while)..."
    apptainer build --fakeroot "${FIR_SIF}.partial" Apptainer.fir.def
    mv "${FIR_SIF}.partial" "${FIR_SIF}"
    echo "      written: ${FIR_SIF} ($(du -h "${FIR_SIF}" | cut -f1))"
fi
echo "      import check (CPU only; CUDA is exercised by a smoke job):"
PYTHONPATH="${SDPO_DIR}:${REPO_ROOT}/src" apptainer exec "${FIR_SIF}" python - <<'PYEOF'
import importlib
for m in ("torch", "vllm", "flash_attn", "flashinfer", "transformers", "peft",
          "ray", "tensordict", "verl", "moralgym_verl"):
    try:
        mod = importlib.import_module(m)
        print(f"        {m:14s} {getattr(mod, '__version__', 'ok')}")
    except Exception as e:  # noqa: BLE001
        print(f"        {m:14s} MISSING ({type(e).__name__}: {e})")
PYEOF

# ── 4. Directories ───────────────────────────────────────────────────────────
echo "[4/4] Directories"
mkdir -p "${MORALGYM_CKPT_ROOT:-${SCRATCH}/moralgym_verl_runs}" \
         "${SCRATCH}/moralgym_verl_datasets" "${HF_HOME}" "${HOME}/logs_verl"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps (scripts/slurm/fir/README.md):"
echo "  1. Model weights (jobs run with HF_HUB_OFFLINE=1; cache HF_HOME=${HF_HOME}):"
echo "     HF_HOME=${HF_HOME} ${FIR_VENV}/bin/hf download Qwen/Qwen3-8B"
echo "  2. Resolve a run, then smoke it (5 steps, 4 GPUs, short walltime):"
echo "     DRY_RUN=1 bash scripts/slurm/fir/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200 qwen3_8b_grpo_pd_deon_tft_smoke"
echo "     TIME=01:00:00 bash scripts/slurm/fir/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200 qwen3_8b_grpo_pd_deon_tft_smoke trainer.total_training_steps=5"
