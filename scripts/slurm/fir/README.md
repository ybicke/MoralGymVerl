# Running on Fir

Fir (Digital Research Alliance of Canada, SFU) is the second cluster next to
Clariden. Nothing Clariden-specific was changed: the launchers in
`scripts/slurm/`, `scripts/setup/setup_verl.sh`, `Dockerfile.gh200` and
`configs/training/_base_clariden.yaml` are the Clariden setup, and this
directory plus `Apptainer.fir.def`, `scripts/setup/setup_fir.sh` and
`configs/training/cluster/fir.yaml` are the Fir setup. Pick by path:

| | Clariden | Fir |
|---|---|---|
| setup | `bash scripts/setup/setup_verl.sh` | `sbatch --account=def-zhijing scripts/setup/setup_fir.sh` |
| train | `bash scripts/slurm/train_verl.sh <run>` | `bash scripts/slurm/fir/train_verl.sh <run>` (`GPUS=1..4` or `mig1|mig2|mig3` for one MIG slice, default 4; `ACCOUNT=` overrides cluster.env) |
| eval sweep | `python3.11 scripts/slurm/submit_sweep.py <spec>` | `~/venvs/moralgym/bin/python scripts/slurm/fir/submit_sweep.py <spec>` |
| benchmarks | `python3.11 scripts/slurm/submit_benchmarks.py <spec>` | `~/venvs/moralgym/bin/python scripts/slurm/fir/submit_benchmarks.py <spec>` |
| single cell | `sbatch scripts/slurm/eval_teacher_signal.sh ...` | `sbatch --gpus-per-node=h100:1 --cpus-per-task=12 --mem=120G scripts/slurm/fir/eval_teacher_signal.sh ...` |
| container | podman → enroot sqsh → EDF, `srun --environment=` | Apptainer SIF, `srun apptainer exec --nv ...` |
| host python | `/usr/bin/python3.11` | `~/venvs/moralgym/bin/python` (+ `module load arrow` for pyarrow) |

Both read the same git-ignored `scripts/slurm/cluster.env`
(`SLURM_ACCOUNT`, `MORALGYM_CKPT_ROOT`, `MORALGYM_STORE_ROOT`, `HF_HOME`,
optional `SDPO_DIR`). The Fir files are copies of the Clariden launchers with
the cluster parts swapped; the header of each says so. When a Clariden
launcher changes, port the change here.

## What differs on Fir

- **Fast smokes**: MIG slices start within minutes while full H100s can queue
  for hours. Verified recipe (job 66094915: 2 steps, checkpoint, 8 min on a 3g.40gb slice):

  ```bash
  MODEL_ID=Qwen/Qwen3-4B GPUS=mig3 TIME=00:30:00 bash scripts/slurm/fir/train_verl.sh \
      qwen3_4b_grpo_pd_deon_tft_150 qwen3_4b_grpo_pd_deon_tft_smoke \
      actor_rollout_ref.model.path=Qwen/Qwen3-4B '++data.apply_chat_template_kwargs.enable_thinking=false' \
      trainer.total_training_steps=2 trainer.save_freq=2 'trainer.logger=[console]'
  ```

  The model swap matters: verl sizes vLLM's KV cache for the model's full context, and the config's
  Qwen3-4B-Instruct-2507 declares 262k tokens (36 GiB for one request), which no slice holds; Qwen3-4B
  has 40k. 8B models do not fit a slice either. This checks the mechanics (container, staging, Ray,
  vLLM, FSDP, checkpointing), not the memory settings of a real run.
  On a slice the launcher sets `CUDA_VISIBLE_DEVICES=0` inside the container, because vLLM
  cannot parse the MIG UUID Slurm puts there.
  It also caps the vLLM memory share at 0.5 on slices (`FIR_MIG_GPU_UTIL=` to change), since the
  run configs are sized for 80 GB.
- **Node**: 4× H100 80 GB SXM, 48 cores, ~1125 GB RAM, 7 TB NVMe at
  `$SLURM_TMPDIR`. One GPU = 12 cores + ~250 GB. Clariden's GH200 has
  97 GB per GPU and CPU offload over NVLink-C2C; the same configs run, but
  `configs/training/cluster/fir.yaml` is the hook for Fir-only overrides
  (appended last via `+cluster=fir`). No run has been smoke-tested on H100
  yet; the 32B memory plan assumes 97 GB.
- **Scheduler**: billing is per GPU (Clariden bills whole nodes), partitions
  are picked from the request, never pass `--partition`, always name the
  GPU type (`--gpus-per-node=h100:N`). Walltime tiers: 3 h, 12 h, 1 d, 3 d,
  7 d; shorter queues faster.
- **Accounts**: `rrg-zhijing` is GPU-only. `~/.bashrc` exports it as
  `SBATCH_ACCOUNT`, and environment variables override `#SBATCH --account`
  lines, so CPU jobs (the setup build) need `--account=def-zhijing` on the
  command line.
- **Storage**: `$HOME` 48 GB; `$SCRATCH` 19 TB, purged after 60 days of
  inactivity; `/project` is a 954 GB group quota, nearly full. Checkpoints,
  datasets, the HF cache and the SIF go to `$SCRATCH`; code and the venv to
  `$HOME`. `$SLURM_TMPDIR` is the container's `/tmp`.
- **Internet**: compute nodes can reach HF, PyPI and W&B (no proxy). Jobs
  still run with `HF_HUB_OFFLINE=1`; download weights first:
  `HF_HOME=$SCRATCH/.cache/huggingface ~/venvs/moralgym/bin/hf download Qwen/Qwen3-8B`.
- **Container**: `Apptainer.fir.def` pulls the amd64 build of the same NGC
  image as `Dockerfile.gh200` and installs the same `requirements-gh200.txt`.
  The SIF is read-only, so SDPO and this repo enter through `PYTHONPATH`
  (set by the launchers), not `pip install -e`. `fir_env.sh` binds
  `/scratch`, `/project` and `$SLURM_TMPDIR:/tmp` and passes `--nv`; the
  container env that Clariden's EDF sets lives in `fir.env`.
- **Login node**: per-user cgroup of 6 cores / 16 GiB; build the image in a
  job.

## Files

| file | role |
|---|---|
| `fir.env` | constants: venv, SIF path, resource flags, container env (bash + python read it) |
| `fir_env.sh` | sources `cluster.env` + `fir.env`, loads modules, defines `FIR_CONTAINER_EXEC` |
| `fir_env.py` | the same for the submit scripts |
| `train_verl.sh` → `train_job.sh` | submit-time checks and dataset generation → batch script that enters the container once |
| `eval_pack.sh`, `benchmark_pack.sh`, `eval_teacher_signal.sh` | Fir variants of the eval launchers |
| `submit_sweep.py`, `submit_benchmarks.py` | Fir variants of the submitters (Fir launchers + resource flags) |

## First run

```bash
cp scripts/slurm/cluster.env.example scripts/slurm/cluster.env   # SLURM_ACCOUNT=rrg-zhijing, HF_HOME on $SCRATCH
sbatch --account=def-zhijing scripts/setup/setup_fir.sh          # SDPO, venv, SIF (~1 h)
HF_HOME=$SCRATCH/.cache/huggingface ~/venvs/moralgym/bin/hf download Qwen/Qwen3-8B
DRY_RUN=1 bash scripts/slurm/fir/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200 qwen3_8b_grpo_pd_deon_tft_smoke
TIME=01:00:00 bash scripts/slurm/fir/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200 qwen3_8b_grpo_pd_deon_tft_smoke trainer.total_training_steps=5
```

Logs land in `~/logs_verl/training/<job>_<run>.{log,err,mem,cmd.sh}`; the
`.cmd.sh` is exactly what ran inside the container.

## Tests

The venv has no torch, so the torch-free part of the suite runs on the login
node and the rest inside the container (no GPU needed):

```bash
PYTHONPATH=src:scripts/analysis ~/venvs/moralgym/bin/python -m pytest tests/ \
    --ignore=tests/test_behavioral_metadata.py --ignore=tests/test_distillation_jsd.py \
    --ignore=tests/test_evaluate_integration.py --ignore=tests/test_probe_episode.py \
    --deselect tests/test_teacher_context.py::test_hint_refused_under_randomized_labels
module load apptainer/1.3.5
PYTHONPATH=src:scripts/analysis:$HOME/SDPO apptainer exec $SCRATCH/containers/moralgym-verl-fir.sif \
    python -m pytest tests/
```
