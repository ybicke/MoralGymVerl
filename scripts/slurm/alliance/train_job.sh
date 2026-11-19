#!/bin/bash
# =============================================================================
# Batch script submitted by scripts/slurm/alliance/train_verl.sh. Runs on the
# compute node OUTSIDE the container and enters it once, for training.
# Everything arrives in the environment (train_verl.sh exports it and
# submits with --export=ALL):
#   REPO_ROOT CONFIG_NAME RUN_NAME EXTRA_ARGS OUTPUT_DIR
#   MORALGYM_DIR SDPO_DIR MORALGYM_CKPT_ROOT MORALGYM_STORE_ROOT
#   MORALGYM_RUN_NAME MORALGYM_GROUP MORALGYM_DATASET_DIR WANDB_API_KEY
#   JUDGE JUDGE_MODEL (JUDGE=1: heterogeneous job, judge on component 1)
# fir_env.sh is sourced again HERE so FIR_CONTAINER_EXEC binds this job's
# $SLURM_TMPDIR, and the container command never crosses sbatch quoting.
# Mirrors the --wrap of scripts/slurm/train_verl.sh (Clariden): version
# line, host-memory sampler, training, stage-out gated on the exit code.
# =============================================================================
set -uo pipefail
. "${REPO_ROOT}/scripts/slurm/alliance/fir_env.sh" "${REPO_ROOT}"

CKPT_DIR="${MORALGYM_CKPT_ROOT}/${RUN_NAME}"
MEM_LOG="${OUTPUT_DIR}/${SLURM_JOB_ID}_${RUN_NAME}.mem"
CMD_FILE="${OUTPUT_DIR}/${SLURM_JOB_ID}_${RUN_NAME}.cmd.sh"

mkdir -p "${CKPT_DIR}"
echo "job=${SLURM_JOB_ID} cluster=${MORALGYM_CLUSTER} $(bash "${REPO_ROOT}/scripts/slurm/code_version.sh" "${MORALGYM_DIR}" "${SDPO_DIR}")" \
    | tee -a "${CKPT_DIR}/code_version.txt"

bash "${REPO_ROOT}/scripts/slurm/mem_sampler.sh" 10 > "${MEM_LOG}" 2>&1 &
MEM_PID=$!

# Judge (JUDGE=1, LLM-judge reward, training/judge.py): train_verl.sh submitted
# a heterogeneous job, component 0 = training, component 1 = one GPU for the
# judge. Start the vLLM judge there now, so it loads while the training
# weights are staged below; training waits for it before starting. Started
# BEFORE staging on purpose: staging sets APPTAINERENV_HF_HOME=/tmp/hf for the
# training container, which does not exist on the judge node. The judge
# container gets no $SLURM_TMPDIR bind (that path belongs to this node).
SRUN_TRAIN=(srun)
if [ "${JUDGE:-0}" = "1" ]; then
    JUDGE_NODE="$(scontrol show hostnames "${SLURM_JOB_NODELIST_HET_GROUP_1:?JUDGE=1 needs a heterogeneous job}" | head -1)"
    JUDGE_PORT=$(( 20000 + SLURM_JOB_ID % 20000 ))
    JUDGE_LOG="${OUTPUT_DIR}/${SLURM_JOB_ID}_${RUN_NAME}.judge.log"
    srun --het-group=1 apptainer exec --nv --pwd "${HOME}" "${FIR_SIF}" \
        bash "${REPO_ROOT}/scripts/slurm/alliance/judge_vllm.sh" "${JUDGE_MODEL}" "${JUDGE_PORT}" \
        > "${JUDGE_LOG}" 2>&1 &
    JUDGE_SRUN=$!
    export MORALGYM_JUDGE_URL="http://${JUDGE_NODE}:${JUDGE_PORT}/v1"
    SRUN_TRAIN=(srun --het-group=0)
    echo "[judge] ${JUDGE_MODEL} on ${JUDGE_NODE}:${JUDGE_PORT} (log ${JUDGE_LOG})"
fi

# Model weights onto node-local NVMe. Eight processes (4 FSDP workers +
# 4 vLLM engines) read the full checkpoint at start-up; from Lustre that
# took 11 min for Qwen3-8B (job 66075174), from $SLURM_TMPDIR it is one
# sequential copy plus local reads. Only refs/ and the resolved snapshot are
# copied (the hub layout HF needs offline; blobs/ would double the size).
# $SLURM_TMPDIR is bound to /tmp INSIDE the container and is not visible
# under its host path there (job 66086764 failed on exactly that), so the
# container's HF_HOME is /tmp/hf, set through APPTAINERENV_ (container-only;
# the host keeps HF_HOME on $SCRATCH). Anything missing falls back to the
# $SCRATCH cache untouched.
if [ "${FIR_STAGE_MODEL:-1}" = "1" ] && [ -n "${MODEL_ID:-}" ] && [ -n "${SLURM_TMPDIR:-}" ]; then
    REPO_DIR="models--${MODEL_ID//\//--}"
    SNAP="${HF_HOME}/hub/${REPO_DIR}"
    REV="$(cat "${SNAP}/refs/main" 2>/dev/null || true)"
    if [ -n "${REV}" ] && [ -d "${SNAP}/snapshots/${REV}" ]; then
        STAGED="${SLURM_TMPDIR}/hf/hub/${REPO_DIR}"
        mkdir -p "${STAGED}/refs" "${STAGED}/snapshots"
        t0=$(date +%s)
        # -L: the snapshot is symlinks into blobs/, copy the files themselves.
        if cp "${SNAP}/refs/main" "${STAGED}/refs/main" && cp -rL "${SNAP}/snapshots/${REV}" "${STAGED}/snapshots/"; then
            echo "[stage] ${MODEL_ID}@${REV:0:8} -> ${STAGED} ($(du -sh "${STAGED}" | cut -f1), $(( $(date +%s) - t0 )) s); container HF_HOME=/tmp/hf"
            export APPTAINERENV_HF_HOME="/tmp/hf"
        else
            echo "[stage] copy failed; reading HF_HOME=${HF_HOME} from scratch"
        fi
    else
        echo "[stage] no local snapshot for ${MODEL_ID} under ${SNAP}; reading HF_HOME=${HF_HOME} from scratch"
    fi
fi

# MIG slice: Slurm sets CUDA_VISIBLE_DEVICES to the slice's UUID
# ("MIG-..."), which vLLM parses as an integer and crashes on (job
# 66091923, vllm/platforms/interface.py device_id_to_physical_device_id).
# With one slice exposed to the job, index 0 addresses it just as well:
# torch, Ray and a matmul verified on a 3g.40gb slice (probe 66092787).
# Container-only, through APPTAINERENV_.
if [ "${FIR_MIG:-0}" = "1" ]; then
    export APPTAINERENV_CUDA_VISIBLE_DEVICES=0
    echo "[mig] CUDA_VISIBLE_DEVICES=0 inside the container (host: ${CUDA_VISIBLE_DEVICES:-unset})"
fi

# In-container command, written to a file: inspectable afterwards, no nested
# quoting. Hydra: the PRIMARY config must resolve from --config-path:
#   <config_name>, _base_clariden -> MoralGymVerl/configs/training/
#   ppo_trainer                   -> SDPO/verl/trainer/config/ (searchpath)
# +cluster=<cluster> appends configs/training/cluster/<cluster>.yaml LAST (after the run
# config), the place for Fir-only overrides; _base_clariden.yaml itself is
# cluster-neutral (env-driven paths, 4-GPU geometry) and is used unchanged.
{
    echo "#!/bin/bash"
    echo "# Generated by scripts/slurm/alliance/train_job.sh for job ${SLURM_JOB_ID} (${RUN_NAME})."
    echo "set -uo pipefail"
    echo "export PYTHONPATH=${SDPO_DIR}:${MORALGYM_DIR}/src"
    echo "exec python -m verl.trainer.main_ppo \\"
    echo "  --config-path ${MORALGYM_DIR}/configs/training \\"
    echo "  --config-name ${CONFIG_NAME} \\"
    echo "  'hydra.searchpath=[file://${SDPO_DIR}/verl/trainer/config]' \\"
    echo "  +cluster=${MORALGYM_CLUSTER} \\"
    echo "  trainer.experiment_name=${RUN_NAME} \\"
    echo "  ${EXTRA_ARGS}"
} > "${CMD_FILE}"
echo "--- ${CMD_FILE} ---"; cat "${CMD_FILE}"; echo "---"
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv

if [ "${JUDGE:-0}" = "1" ]; then
    for i in $(seq 1 120); do
        curl -sf "http://${JUDGE_NODE}:${JUDGE_PORT}/health" >/dev/null && break
        kill -0 "${JUDGE_SRUN}" 2>/dev/null || { echo "[judge] server died, see ${JUDGE_LOG}" >&2; exit 1; }
        sleep 5
    done
    curl -sf "http://${JUDGE_NODE}:${JUDGE_PORT}/health" >/dev/null \
        || { echo "[judge] not up after 10 min, see ${JUDGE_LOG}" >&2; exit 1; }
    echo "[judge] up: ${MORALGYM_JUDGE_URL}"
fi

# shellcheck disable=SC2086
"${SRUN_TRAIN[@]}" ${FIR_CONTAINER_EXEC} bash "${CMD_FILE}"
RC=$?
[ "${JUDGE:-0}" = "1" ] && kill "${JUDGE_SRUN}" 2>/dev/null

kill "${MEM_PID}" 2>/dev/null
echo "[mem] peak: $(tail -1 "${MEM_LOG}")"

if [ ${RC} -eq 0 ]; then
    if [ -n "${MORALGYM_STORE_ROOT:-}" ]; then
        STORE_CKPT="${MORALGYM_STORE_ROOT}/moralgym_verl/checkpoints/${RUN_NAME}"
        if [ -d "${CKPT_DIR}" ]; then
            mkdir -p "${STORE_CKPT}" && cp -r "${CKPT_DIR}/." "${STORE_CKPT}/" \
                && echo "Checkpoints staged to ${STORE_CKPT}"
        else
            echo "No checkpoint dir at ${CKPT_DIR}, skipping stage-out"
        fi
    else
        echo "MORALGYM_STORE_ROOT not set, checkpoints stay in ${CKPT_DIR}"
    fi
fi
exit ${RC}
