#!/bin/bash
# =============================================================================
# FIR VARIANT of scripts/slurm/train_verl.sh (Clariden original left untouched).
# Differences: Apptainer instead of EDF (FIR_CONTAINER_EXEC), Fir resource
# flags from scripts/slurm/fir/fir.env, host python from the venv. Keep the
# rest in step with the original when it changes.
# =============================================================================
# Submit a MoralGymVerl GRPO or SDPO training job on Fir.
#
# Usage:
#   bash scripts/slurm/fir/train_verl.sh <config_name> [run_name] [verl overrides...]
#
# Naming contract (docs/naming.md): configs/training/<RUN_NAME>.yaml is one
# file per run, named with the canonical grammar
#   <model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>[_vN]
# so RUN_NAME defaults to <config_name> and everything downstream (W&B,
# checkpoint dir, logs, dataset dir, post-training eval subject) shares it.
# DATASET_CONFIG defaults to the <algo>_<game>_<arm>_<opponent> fields of
# the run name. Pass [run_name] only to deviate (smokes: *_smoke).
#
# Examples:
#   bash train_verl.sh qwen3_8b_grpo_pd_util_tft_150
#   DRY_RUN=1 bash train_verl.sh qwen3_8b_grpo_pd_util_tft_150   # resolve only
#
# Trailing args become Hydra overrides. Env: ACCOUNT (cluster.env's
# SLURM_ACCOUNT), GPUS (4; Fir bills per GPU, so
# GPUS=1 or 2 is fine for small models — verl runs that many vLLM replicas
# and FSDP ranks; GPUS=mig3 = one 40 GB MIG slice, the fast debug path), TIME (12:00:00), MEM (250G per GPU, 1000G = the whole
# node), DATASET_CONFIG (derived, also names the W&B group),
# DATASET_SEED (42), SEED (unset; replicate N of the config, see step 1),
# DRY_RUN (stop after name/phase checks; no dataset generation, no sbatch).
#
#   SEED=1 bash train_verl.sh qwen3_8b_grpo_pd_deon_tft_200
#   -> run qwen3_8b_grpo_pd_deon_tft_200_seed1, every RNG below seeded 1
#
# Three execution contexts — every path below depends on which one it is in:
#   1-4  login node, submit time   venv python, no GPUs
#   5-6  login node                exports for the batch script
#   7    compute node              scripts/slurm/fir/train_job.sh: training
#                                  INSIDE the Apptainer container, stage-out
#                                  outside it
# =============================================================================
set -euo pipefail

# ── 1. Arguments ─────────────────────────────────────────────────────────────
CONFIG_NAME="${1:?Usage: train_verl.sh <config_name> [run_name] [overrides...]}"
shift
# Optional run_name: a Hydra override always contains '=', a run name never.
if [ $# -gt 0 ] && [[ "$1" != *=* ]]; then
    RUN_NAME="$1"; shift
else
    RUN_NAME="${CONFIG_NAME}"
fi
EXTRA_ARGS="$@"

# Seed replicates. SEED=N runs replicate N of the config: the name gets a
# _seed<N> suffix, so the run owns its checkpoint, dataset and log dirs
# (resume_mode auto would otherwise resume another seed's checkpoints), the
# training data is generated from seed N, and the RNGs that verl leaves at
# fixed defaults are set to N:
#   data.seed                          prompt order (default null: torch's
#                                      default generator, same order every run)
#   actor.data_loader_seed             minibatch shuffle (default 42)
#   rollout.engine_kwargs.vllm.seed    vLLM sampling; `rollout.seed` is not a
#                                      config field, the engine falls back to
#                                      0 (vllm_async_server.py:318), and
#                                      engine_kwargs is spread last so it wins
# LoRA init is not seeded (peft init under torch's default generator, the
# same for every run). Sampling is not bitwise reproducible either (vLLM
# batching), so seeds are independent replicates, not exact reruns.
if [ -n "${SEED:-}" ]; then
    RUN_NAME="${RUN_NAME}_seed${SEED}"
    DATASET_SEED="${DATASET_SEED:-${SEED}}"
    EXTRA_ARGS="data.seed=${SEED} \
actor_rollout_ref.actor.data_loader_seed=${SEED} \
++actor_rollout_ref.rollout.engine_kwargs.vllm.seed=${SEED} \
${EXTRA_ARGS}"
fi

# Canonical run-name check (docs/naming.md). A canonical name also yields
# the dataset config: fields 3-6 = <algo>_<game>_<arm>_<opponent>.
if [[ "${RUN_NAME}" =~ ^([a-z0-9]+)_([0-9]+b)_(grpo|sdpo)_([a-z0-9]+)_([a-z0-9-]+)_([a-z0-9-]+)_([0-9]+|smoke[0-9]*)(_v[0-9]+)?(_seed[0-9]+)?$ ]]; then
    DATASET_DEFAULT="${BASH_REMATCH[3]}_${BASH_REMATCH[4]}_${BASH_REMATCH[5]}_${BASH_REMATCH[6]}"
else
    echo "WARNING: run name '${RUN_NAME}' is not canonical" >&2
    echo "  expected <model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>[_vN][_seedN] (docs/naming.md)" >&2
    DATASET_DEFAULT="${CONFIG_NAME}"
fi

# Every run that is not a smoke is a seeded replicate; unseeded, verl falls
# back to one fixed prompt order and sampling seed for all such runs.
if [ -z "${SEED:-}" ] && [[ "${BASH_REMATCH[7]:-}" != smoke* ]]; then
    echo "ERROR: ${RUN_NAME} has no seed. Set SEED=N; only *_smoke runs may be unseeded." >&2
    exit 1
fi

# ── 2. Cluster settings ──────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# Account, checkpoint and store roots, source checkouts: scripts/slurm/cluster.env;
# Fir constants (venv python, SIF, resource flags): scripts/slurm/fir/fir.env.
. "${REPO_ROOT}/scripts/slurm/fir/fir_env.sh" "${REPO_ROOT}"
# ACCOUNT=<name> in the env overrides cluster.env for one submit, e.g. a
# smoke under the lightly used def-zhijing (fair-share decides queue
# position on Fir; the RAC account carries the group's whole usage).
ACCOUNT="${ACCOUNT:-${SLURM_ACCOUNT:?set SLURM_ACCOUNT in scripts/slurm/cluster.env}}"
MORALGYM_DIR="${MORALGYM_DIR:-${REPO_ROOT}}"
SDPO_DIR="${SDPO_DIR:-${HOME}/SDPO}"
MORALGYM_CKPT_ROOT="${MORALGYM_CKPT_ROOT:-${SCRATCH}/moralgym_verl_runs}"
TIME="${TIME:-12:00:00}"
# GPUs per run. One GPU on Fir comes with 12 cores and ~250 GB host RAM;
# 4 GPUs = the whole node. The trainer is told the same number (the config
# default is 4, from _base_clariden.yaml).
# GPUS=mig1|mig2|mig3 instead takes ONE MIG slice of an H100 (10/20/40 GB,
# 1/3/6 cores): slices start within minutes while full H100s queue for
# hours, so this is the debug path for smokes of models that fit (Qwen3-4B
# on mig3 with the stock settings; 8B does not fit a 40 GB slice at the
# configured vLLM share). One slice per job is a Fir rule, and a slice has
# no NVLink, so the trainer runs single-GPU.
GPUS="${GPUS:-${FIR_NODE_GPUS}}"
case "${GPUS}" in
    mig1) GRES="nvidia_h100_80gb_hbm3_1g.10gb:1"; CPUS=1; MEM="${MEM:-32G}";  N_GPUS=1 ;;
    mig2) GRES="nvidia_h100_80gb_hbm3_2g.20gb:1"; CPUS=3; MEM="${MEM:-64G}";  N_GPUS=1 ;;
    mig3) GRES="nvidia_h100_80gb_hbm3_3g.40gb:1"; CPUS=6; MEM="${MEM:-120G}"; N_GPUS=1 ;;
    [1-4]) GRES="h100:${GPUS}"; CPUS=$(( GPUS * FIR_NODE_CPUS / FIR_NODE_GPUS )); MEM="${MEM:-$(( GPUS * 250 ))G}"; N_GPUS="${GPUS}" ;;
    *) echo "ERROR: GPUS must be 1-4 or mig1|mig2|mig3, got '${GPUS}'" >&2; exit 1 ;;
esac
EXTRA_ARGS="trainer.n_gpus_per_node=${N_GPUS} ${EXTRA_ARGS}"
# train_job.sh needs to know (MIG slices need CUDA_VISIBLE_DEVICES=0 inside
# the container, see there).
case "${GPUS}" in mig*) export FIR_MIG=1 ;; *) export FIR_MIG=0 ;; esac
# A slice has far less memory than the 80 GB the run configs are sized for:
# with the FSDP actor resident, a 3g.40gb slice had 23.1 GiB free at vLLM
# start-up and the 4B config's 0.6 share asked for 23.7 (job 66093423).
# Cap the vLLM share on slices; FIR_MIG_GPU_UTIL=<x> or a trailing
# actor_rollout_ref.rollout.gpu_memory_utilization=<x> override wins.
if [ "${FIR_MIG}" = "1" ]; then
    EXTRA_ARGS="actor_rollout_ref.rollout.gpu_memory_utilization=${FIR_MIG_GPU_UTIL:-0.5} ${EXTRA_ARGS}"
fi

# ── 3. Per-run dataset: paths ────────────────────────────────────────────────
# Regenerated every submit (step 4) so the parquet cannot go stale against the
# config — 2026-07-06 SDPO trained on a 4-day-old parquet with a superseded
# lambda/bias. On SCRATCH: no HOME quota, and the 30-day purge is the cleanup,
# since config + seed regenerate it bit-identically.
DATASET_SEED="${DATASET_SEED:-42}"
# DATASET_CONFIG decouples data from trainer, e.g. SDPO on the GRPO dataset.
# It also names the W&B group (step 8): with one reward-agnostic trainer
# config shared by several arms, the DATASET is the arm identity — which is
# why it is derived from the arm fields of the canonical run name (step 1).
DATASET_CONFIG="${DATASET_CONFIG:-${DATASET_DEFAULT}}"
DATASET_DIR="${SCRATCH}/moralgym_verl_datasets/${RUN_NAME}"

# ── 3b. Resume ───────────────────────────────────────────────────────────────
# A run folder with checkpoints is resumed from its latest one. verl restores
# optimizer, step and data position; the LoRA adapters come back only through
# lora_adapter_path (the EMA teacher's is read from next to the student's).
LATEST="${MORALGYM_CKPT_ROOT}/${RUN_NAME}/latest_checkpointed_iteration.txt"
if [ -f "${LATEST}" ]; then
    RESUME_DIR="${MORALGYM_CKPT_ROOT}/${RUN_NAME}/global_step_$(cat "${LATEST}")/actor"
    if [[ "${RUN_NAME}" == *_sdpo_* ]] && [ ! -d "${RESUME_DIR}/teacher_lora_adapter" ]; then
        echo "ERROR: ${RESUME_DIR} has no teacher_lora_adapter, so this SDPO run cannot be resumed." >&2
        exit 1
    fi
    EXTRA_ARGS="actor_rollout_ref.model.lora_adapter_path=${RESUME_DIR}/lora_adapter ${EXTRA_ARGS}"
    echo "Resuming ${RUN_NAME} from step $(cat "${LATEST}")"
fi

# ── 4. Per-run dataset: phase guard, then generate ───────────────────────────
# Turn structure lives in TWO files that must agree — dataset game.num_rounds
# and trainer multi_turn.enable — and a mismatch fails SILENTLY: multi-round
# data under a single-turn trainer generates one turn against a prompt written
# for five; 1-round data under the agent loop stops after round 1. Easy to hit
# via DATASET_CONFIG.
"${FIR_HOST_PYTHON}" - "${REPO_ROOT}" "${DATASET_CONFIG}" "${CONFIG_NAME}" <<'PYEOF'
import sys, yaml
repo, ds_name, tr_name = sys.argv[1:4]
ds = yaml.safe_load(open(f"{repo}/configs/datasets/{ds_name}.yaml"))
tr = yaml.safe_load(open(f"{repo}/configs/training/{tr_name}.yaml"))
rounds = ds["game"]["num_rounds"]
mt = (tr.get("actor_rollout_ref", {}).get("rollout", {})
        .get("multi_turn", {}).get("enable", False))
if (rounds > 1) != bool(mt):
    sys.exit(
        f"ERROR: turn-structure mismatch — dataset {ds_name}.yaml has "
        f"num_rounds={rounds} but trainer {tr_name}.yaml has "
        f"multi_turn.enable={mt}.\n"
        f"  single-turn: num_rounds=1 + multi_turn.enable unset/false\n"
        f"  multi-turn:  num_rounds>1 + multi_turn.enable=true "
        f"(+ MoralGymRewardManager)"
    )
print(f"Phase check OK: num_rounds={rounds}, multi_turn.enable={bool(mt)}")
# game.per_batch holds only if the trainer reads one dataset block per batch.
per_batch = ds["game"].get("per_batch")
data = tr.get("data", {})
if per_batch and (data.get("train_batch_size") != sum(per_batch)
                  or data.get("shuffle") is not False):
    sys.exit(
        f"ERROR: dataset {ds_name}.yaml has game.per_batch={per_batch}; trainer "
        f"{tr_name}.yaml must set data.train_batch_size={sum(per_batch)} and "
        f"data.shuffle=false."
    )
PYEOF

if [ -n "${DRY_RUN:-}" ]; then
    echo "DRY_RUN resolution:"
    echo "  config:  configs/training/${CONFIG_NAME}.yaml"
    echo "  gpus:    ${GRES} (${CPUS} cores, ${MEM})"
    echo "  run:     ${RUN_NAME}"
    echo "  dataset: configs/datasets/${DATASET_CONFIG}.yaml (seed ${DATASET_SEED})"
    echo "  ckpts:   ${MORALGYM_CKPT_ROOT}/${RUN_NAME}"
    echo "  seed:    ${SEED:-unset (verl defaults: data.seed null, data_loader_seed 42, vllm seed 0)}"
    echo "  hydra:   ${EXTRA_ARGS:-<none>}"
    echo "Stopping before dataset generation and sbatch."
    exit 0
fi

# System python3 on the login node is 3.9 (no pandas/pyarrow); use the venv.
# pyarrow comes from the arrow module through PYTHONPATH (fir_env.sh loads
# it), so the host PYTHONPATH is appended, not replaced.
echo "Generating dataset from configs/datasets/${DATASET_CONFIG}.yaml (seed ${DATASET_SEED})"
PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" "${FIR_HOST_PYTHON}" -m moralgym_verl.training.dataset \
    --config "${REPO_ROOT}/configs/datasets/${DATASET_CONFIG}.yaml" \
    --output-dir "${DATASET_DIR}" \
    --n-train 8000 --n-val 256 --seed "${DATASET_SEED}"

# ── 5. Slurm log destination ─────────────────────────────────────────────────
# ~/logs_verl splits by stage: pre_eval (base-model screens), training (here),
# eval (post-training checkpoints). HOME, so the SCRATCH purge cannot reach it.
OUTPUT_DIR="${HOME}/logs_verl/training"
mkdir -p "${OUTPUT_DIR}"

# ── 6. What the batch script needs ─────────────────────────────────────────
# scripts/slurm/fir/train_job.sh runs on the compute node: it re-sources
# fir_env.sh there, writes the in-container command to
# ${OUTPUT_DIR}/<job>_<run>.cmd.sh, samples host memory, and stages out.
# It reads everything from the environment, hence export + --export=ALL.
# The run identity also reaches the config inside the container, where
# _base_clariden.yaml reads it via oc.env (MORALGYM_RUN_NAME/GROUP/DATASET_DIR).
# The model id, for staging its HF snapshot to node-local disk in
# train_job.sh: actor_rollout_ref.model.path from the run config, else from
# the nearest file in its defaults chain (how Hydra would resolve it, minus
# command-line overrides — MODEL_ID=<org/name> in the env wins if given).
MODEL_ID="${MODEL_ID:-$("${FIR_HOST_PYTHON}" - "${REPO_ROOT}/configs/training" "${CONFIG_NAME}" <<'MODELEOF'
import sys, yaml, pathlib
root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
def find(cfg_name, seen=()):
    f = root / f"{cfg_name}.yaml"
    if cfg_name in seen or not f.is_file():
        return None
    cfg = yaml.safe_load(f.read_text()) or {}
    path = ((cfg.get("actor_rollout_ref") or {}).get("model") or {}).get("path")
    if path:
        return path
    for d in reversed(cfg.get("defaults") or []):
        if isinstance(d, str) and d not in ("_self_", "ppo_trainer"):
            found = find(d, seen + (cfg_name,))
            if found:
                return found
    return None
print(find(name) or "")
MODELEOF
)}"
echo "  model:   ${MODEL_ID:-<unresolved: no staging>}"
export MODEL_ID
export REPO_ROOT CONFIG_NAME RUN_NAME EXTRA_ARGS OUTPUT_DIR
export MORALGYM_DIR SDPO_DIR MORALGYM_CKPT_ROOT MORALGYM_STORE_ROOT="${MORALGYM_STORE_ROOT:-}"
export MORALGYM_RUN_NAME="${RUN_NAME}" MORALGYM_GROUP="${DATASET_CONFIG}" MORALGYM_DATASET_DIR="${DATASET_DIR}"

# ── 7. Submit ────────────────────────────────────────────────────────────────
echo "Submitting: ${RUN_NAME} (Fir)"
echo "  config:  configs/training/${CONFIG_NAME}.yaml"
echo "  dataset: ${DATASET_DIR}"
echo ""

# One task owns the allocation: verl spawns its own Ray workers across the
# GPUs. No --partition on Fir (routed by GPUs and walltime); the GPU type
# must be named.
sbatch \
    --job-name="mg-verl" \
    --account="${ACCOUNT}" \
    --nodes=1 \
    --time="${TIME}" \
    --gpus="${GRES}" \
    --ntasks-per-node=1 \
    --cpus-per-task="${CPUS}" \
    --mem="${MEM}" \
    --output="${OUTPUT_DIR}/%j_${RUN_NAME}.log" \
    --error="${OUTPUT_DIR}/%j_${RUN_NAME}.err" \
    --export=ALL \
    "${REPO_ROOT}/scripts/slurm/fir/train_job.sh"
