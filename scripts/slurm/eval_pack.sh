#!/bin/bash
#SBATCH --job-name=eval-pack
#SBATCH --account=aa004
#SBATCH --partition=normal
#SBATCH --output=/dev/null
#SBATCH --error=/dev/null
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=4
#SBATCH --gpus-per-node=4
#SBATCH -C thp_never&nvidia_vboost_enabled
#SBATCH --time=05:00:00

# =============================================================================
# PACKED eval launcher — up to 4 eval cells concurrently on one node.
#
# Why this exists: Clariden partitions are OverSubscribe=EXCLUSIVE, so a
# 1-GPU job is billed for the whole 4-GPU node (verified: a single-GPU eval
# job reported AllocTRES gres/gpu=4, cpu=288, node=1). Since fairshare is
# the dominant priority weight, running one cell per node consumes 4x the
# node-hours needed AND degrades future queue priority. Packing 4 cells per
# node cuts both by ~75%.
#
# Mechanism (validated by scripts/debug/pack_test.sh):
#   --overlap, NOT --exact. With --exact the steps SERIALIZE (measured:
#   4x35s sequential instead of concurrent). Slurm also does not isolate
#   GPUs per step here — every step saw all 4 devices — so each cell pins
#   its own via CUDA_VISIBLE_DEVICES.
#
# Each cell runs its phases SEQUENTIALLY inside its own step (behavioral ->
# probe A -> probe B): a job step cannot spawn nested srun steps, so the
# phase sequence that used to live in the host-side driver now lives in the
# per-cell script.
#
# Usage (normally via scripts/slurm/submit_sweep.py):
#   sbatch scripts/slurm/eval_pack.sh <batch.json>
#
# batch.json is written by submit_sweep.py: a list of cells, each with the
# run-dir NAME ALREADY RESOLVED. Deriving that name here as well would mean
# two implementations that can disagree — and with 4 cells sharing one
# SLURM_JOB_ID, a disagreement means two cells writing to one directory.
# =============================================================================

set -uo pipefail

export PROJECT_ROOT="${SLURM_SUBMIT_DIR}"
if [ ! -f "${PROJECT_ROOT}/pyproject.toml" ]; then
    echo "ERROR: sbatch must be run from the MoralGymVerl repo root." >&2
    exit 1
fi
export STORE_BASE="/capstor/store/cscs/swissai/aa004/${USER}"

BATCH_JSON="${1:?Usage: eval_pack.sh <batch.json>}"

# Logs: ~/logs_verl/{pre_eval,training,eval}. Base-model screens (the
# default) go to pre_eval; submit with EVAL_STAGE=eval for post-training
# checkpoint evals.
LOG_BASE="${HOME}/logs_verl/${EVAL_STAGE:-pre_eval}"
mkdir -p "${LOG_BASE}"
exec > "${LOG_BASE}/eval_pack_${SLURM_JOB_ID}.out" 2>&1

CONFIG="${CONFIG:-configs/eval/teacher_signal_9b.yaml}"
WORKDIR="${LOG_BASE}/pack_${SLURM_JOB_ID}"
mkdir -p "${WORKDIR}"

echo "============================================="
echo "Packed eval batch"
echo "Job ID:   ${SLURM_JOB_ID}"
echo "Node:     ${SLURM_NODELIST}"
echo "Batch:    ${BATCH_JSON}"
echo "Started:  $(date)"
echo "============================================="
nvidia-smi --query-gpu=index,name,driver_version --format=csv

# Generate one self-contained script per cell. Writing scripts rather than
# building srun command lines avoids every quoting hazard (moral values
# contain '+', args contain spaces) and leaves an inspectable artifact:
# cat ${WORKDIR}/cell_N.sh shows exactly what that cell ran.
/usr/bin/python3.11 - "${BATCH_JSON}" "${WORKDIR}" "${PROJECT_ROOT}" "${CONFIG}" <<'PYEOF'
import json, os, shlex, sys
batch_path, workdir, root, config = sys.argv[1:5]
CKPT_ROOT = os.environ.get("CKPT_ROOT",
                           f"/iopsstor/scratch/cscs/{os.environ['USER']}/moralgym_verl_runs")

def resolve_checkpoint(value):
    """'base' | absolute adapter dir | '<run>/global_step_N' (verl layout)."""
    if value == "base" or value.startswith("/"):
        return value
    return f"{CKPT_ROOT}/{value}/actor/lora_adapter"
cells = json.load(open(batch_path))["cells"]

for i, c in enumerate(cells):
    # cells/ keeps the machine-readable results in one place, so the group
    # root holds only the manifest, the batch payloads and analysis/.
    results = c.get("results_dir", "teacher_signal")
    run_dir = f"{root}/eval_results/{results}/{c['eval_group']}/cells/{c['run_dir_stem']}_$SLURM_JOB_ID"
    env = c.get("env", {})
    args = [str(a) for a in c.get("args", [])]
    # A sweep's `config:` key travels in the cell env (sweep.cell_submission).
    # The job-level CONFIG is only the fallback: the packed submit path does
    # not export env, so reading it alone would silently run the default
    # config for every cell.
    cell_config = env.get("CONFIG", config)

    def flag(name, key):
        return f'--{name} {shlex.quote(env[key])}' if env.get(key) else ''

    common = " ".join(filter(None, [
        f'--config {shlex.quote(root)}/{shlex.quote(cell_config)}',
        f'--checkpoint {shlex.quote(resolve_checkpoint(env.get("CHECKPOINT", "base")))}',
        f'--game {shlex.quote(c["game"])}',
        f'--moral-value {shlex.quote(c["moral_value"])}',
        flag('model', 'MODEL'),
        flag('protocol', 'PROTOCOL'),
        flag('representation', 'REPRESENTATION'),
    ]))

    lines = [
        '#!/bin/bash',
        '# Generated by eval_pack.sh — one cell, phases in sequence.',
        'set -uo pipefail',
        f'export CUDA_VISIBLE_DEVICES={i}',
        f'RUN_DIR="{run_dir}"',
        'mkdir -p "${RUN_DIR}"',
        f'echo "[cell {i}] game={c["game"]} moral_value={c["moral_value"]} '
        f'env={json.dumps(env)} args={json.dumps(args)}"',
        'echo "[cell %d] CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES start $(date +%%T)"' % i,
        # --- behavioral ---
        f'python3 -m moralgym_verl.eval.behavioral {common} '
        f'{flag("game-description", "GAME_DESCRIPTION")} '
        f'--num-episodes {int(c["num_episodes"])} --save-raw-responses '
        f'--output "${{RUN_DIR}}/behavioral.json" {" ".join(shlex.quote(a) for a in args)} '
        f'|| {{ echo "[cell {i}] BEHAVIORAL FAILED"; exit 1; }}',
        f'echo "[cell {i}] behavioral done $(date +%T)"',
    ]

    # Probes: same gating as the single-cell launcher — skipped for the
    # plain baseline (they compare against the plain prompt internally) and
    # when RUN_PROBES=off.
    if c["moral_value"] != "none" and env.get("RUN_PROBES", "on") != "off":
        for probe in ("probe_a", "probe_b"):
            extra = '--states fabricated ' if probe == "probe_b" else ''
            temp = (f'--temperature {shlex.quote(env["PROBE_TEMPERATURE"])} '
                    if probe == "probe_b" and env.get("PROBE_TEMPERATURE") else '')
            lines += [
                f'python3 -m moralgym_verl.eval.{probe} {common} {extra}{temp}'
                f'--output-dir "${{RUN_DIR}}" '
                f'|| {{ echo "[cell {i}] {probe.upper()} FAILED"; exit 1; }}',
                f'echo "[cell {i}] {probe} done $(date +%T)"',
            ]

    lines.append(f'echo "[cell {i}] ALL PHASES OK $(date +%T)"')
    with open(f"{workdir}/cell_{i}.sh", "w") as f:
        f.write("\n".join(lines) + "\n")

print(f"generated {len(cells)} cell scripts in {workdir}")
PYEOF

NCELLS=$(ls "${WORKDIR}"/cell_*.sh 2>/dev/null | wc -l)
if [ "${NCELLS}" -eq 0 ]; then
    echo "ERROR: no cell scripts generated" >&2
    exit 1
fi
echo "Launching ${NCELLS} cells concurrently"
echo

pids=()
for i in $(seq 0 $((NCELLS - 1))); do
    # --overlap is load-bearing: with --exact these steps serialize.
    srun --overlap --ntasks=1 --cpus-per-task=64 \
         --environment=moralgym_verl \
         bash "${WORKDIR}/cell_${i}.sh" > "${WORKDIR}/cell_${i}.log" 2>&1 &
    pids+=($!)
done

# Per-cell exit codes. A bare `wait` would swallow a failing cell entirely —
# with 4 cells per job, a silent failure is 4x more costly to miss.
failed=()
for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
        failed+=("$i")
    fi
done

echo
echo "=== per-cell output ==="
for i in $(seq 0 $((NCELLS - 1))); do
    echo "--- cell ${i} ---"
    cat "${WORKDIR}/cell_${i}.log"
done

# Stage out to $STORE (tape-backed) per cell, mirroring the single-cell
# launcher. Only whole run dirs that exist — a failed cell may have none.
if [ -n "${STORE_BASE:-}" ]; then
    /usr/bin/python3.11 - "${BATCH_JSON}" "${PROJECT_ROOT}" "${SLURM_JOB_ID}" "${STORE_BASE}" <<'PYEOF'
import json, os, shutil, sys
batch_path, root, job_id, store = sys.argv[1:5]
for c in json.load(open(batch_path))["cells"]:
    results = c.get("results_dir", "teacher_signal")
    # cells/ subdir, matching where the cell scripts write (a missing
    # /cells/ here made every packed stage-out a silent no-op before
    # 2026-08-24).
    src = f"{root}/eval_results/{results}/{c['eval_group']}/cells/{c['run_dir_stem']}_{job_id}"
    if not os.path.isdir(src):
        continue
    dst = f"{store}/eval_results/{results}/{c['eval_group']}/cells"
    os.makedirs(dst, exist_ok=True)
    shutil.copytree(src, f"{dst}/{os.path.basename(src)}", dirs_exist_ok=True)
    print(f"staged {os.path.basename(src)}")
PYEOF
fi

echo
echo "Finished: $(date)"
if [ "${#failed[@]}" -gt 0 ]; then
    echo "FAILED cells: ${failed[*]} (scripts + logs in ${WORKDIR})"
    exit 1
fi
echo "All ${NCELLS} cells OK"
