# Environment for the Alliance (Apptainer) launchers in scripts/slurm/fir/,
# which serve Fir and Rorqual (same 4x H100 nodes, same Slurm setup).
# Usage (sourced): . scripts/slurm/fir/fir_env.sh <repo_root>
#   1. scripts/slurm/cluster.env via the shared cluster_env.sh (account,
#      checkpoint root, HF_HOME, ...; unchanged from the Clariden setup)
#   2. scripts/slurm/fir/<cluster>.env (node constants), every key exported.
#      Cluster = MORALGYM_CLUSTER, else the Alliance-set CC_CLUSTER (set on
#      login and compute nodes), else fir.
#   (steps 3-4 below)
#   3. modules for the host python, pyarrow and Apptainer
#   4. FIR_CONTAINER_EXEC: how a command enters the container. Sourced inside
#      the job too, so ${SLURM_TMPDIR} resolves on the compute node.
# fir_env.py is the python twin for the submit scripts; keep them in step.
_fir_root="${1:?fir_env.sh needs the repo root}"
. "${_fir_root}/scripts/slurm/cluster_env.sh" "${_fir_root}"
export HF_HOME="${HF_HOME:-${SCRATCH}/.cache/huggingface}"
export MORALGYM_CLUSTER="${MORALGYM_CLUSTER:-${CC_CLUSTER:-fir}}"
_fir_envfile="${_fir_root}/scripts/slurm/fir/${MORALGYM_CLUSTER}.env"
if [ ! -f "${_fir_envfile}" ]; then
    echo "ERROR: no settings for cluster '${MORALGYM_CLUSTER}' (${_fir_envfile})" >&2
    return 1 2>/dev/null || exit 1
fi
set -a
# shellcheck disable=SC1090
. "${_fir_envfile}"
set +a

if type module >/dev/null 2>&1; then
    for _fir_m in ${FIR_MODULES}; do
        module load "${_fir_m}" >/dev/null 2>&1 || echo "WARNING: module load ${_fir_m} failed" >&2
    done
fi

# --nv: NVIDIA driver libs. Fir's Apptainer config binds $HOME, /scratch
# and /project by itself (an explicit -B only prints a warning). $SLURM_TMPDIR
# (node NVMe) becomes the container's /tmp: Ray, vLLM and Triton scratch stay
# off Lustre and off the RAM-backed host /tmp, and staged data is addressed
# as /tmp/... from inside. On the login node it falls back to /tmp.
# The SIF is read-only: code comes in through PYTHONPATH from the mounted
# checkouts; nothing is pip-installed at job start.
# --pwd $HOME mirrors the Clariden EDF (workdir = $HOME): Hydra writes its
# per-run outputs/ under the working directory, and the launchers pass only
# absolute paths, so this keeps outputs/ out of the repo checkout.
export FIR_CONTAINER_EXEC="apptainer exec --nv -B ${SLURM_TMPDIR:-/tmp}:/tmp --pwd ${HOME} ${FIR_SIF}"
unset _fir_root _fir_m _fir_envfile
