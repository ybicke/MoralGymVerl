# MoralGym: Fine-Tuning for Cooperation in Multi-Agent Systems

Training language-model agents to cooperate in multi-agent systems through reasoning post-training on games: online RL with RLVR (GRPO) and RLRF self-distillation (SDPO) in [verl](https://github.com/volcengine/verl).

## Overview

LLM agents increasingly act in multi-agent systems, negotiating, coordinating, and competing with other agents whose goals only partly align with their own and may conflict outright. Training for cooperation in such mixed-motive settings is the target, and several approaches are possible. The first attempt trains cooperation guided by moral principles, with the hope that agents learn what is safe for the agent population, so that it does not collapse, and for their interaction environment, so that they do not collude. The project investigates online RL on games with reasoning fine-tuning algorithms: RLVR methods like GRPO and RLRF methods like SDPO.

Game-theoretic games are the first testbed. They are small, their equilibria are known, and cooperation in them has a precise meaning. Training so far is single-step: one Prisoner's Dilemma decision against tit-for-tat, conditioned on a fabricated previous round. Multi-round play against scripted opponents and an n-player Public Goods Game are used for evaluation. Stag Hunt and Chicken are implemented, and the environment is designed to admit richer games later, from negotiation to multi-agent coordination.

A moral principle such as *do not defect on a cooperator* is delivered through one of two algorithms that share the same environment:

- **Reward (GRPO).** Per-round reward is game payoff plus a weighted intrinsic term motivated by the moral principle.
- **Teacher (SDPO).** The moral principle is given in a teacher context; the student distills the teacher's next-token distribution.

SDPO is attractive for two reasons. A plain-language principle can carry over to other games more easily than a reward hand-crafted per game, and per-token credit assignment should help multi-turn training.

Evaluation asks whether the moral principle became a disposition: does trained behaviour track it conditionally, survive changes in prompt presentation, show up in the model's reasoning, and carry over to held-out games and multi-round play?

## Research questions

1. **Cooperation from games.** Can training on games make an LLM a cooperative agent in a multi-agent system?
2. **Generalisation.** Does cooperative behaviour learned from games transfer to unseen environments?
3. **Moral principles as the signal.** Can cooperation in mixed-motive games, social dilemmas, and other games be taught from moral principles, delivered as implicit reward or as moral teaching feedback in plain text?
4. **Safety.** How can cooperative behaviour in multi-agent systems be made safe, so that agents do not collude and the system does not collapse?
5. **Beyond single-step games.** Can the approach extend to multi-agent training with learning co-players, to long-horizon tasks, and to continual learning across games with self-distillation?

## Status

A first exploratory study compares GRPO and SDPO on the single-step Prisoner's Dilemma with Qwen3-8B (partial replication on Gemma and Llama). Both learn reciprocity. GRPO's reasoning shows no moral language, while SDPO's does and its cooperation is more forgiving but more exploitable. In a first transfer test on a four-player Public Goods Game, SDPO's cooperation carries over and GRPO's does not. Matrix games are likely too narrow for broad transfer.

## How it works

1. **Dataset.** `moralgym_verl.training.dataset` samples episodes and writes verl-format parquet prompts; the game state travels as the ground truth. The launcher generates it at submit time.
2. **Rollout and reward.** verl generates a response per prompt. `reward_fn` parses the move, returns payoff plus intrinsic reward, and for SDPO the teacher feedback: either an outcome critique or a fixed moral principle (the reported runs use the principle).
3. **Update.** GRPO or SDPO in the verl fork [lasgroup/SDPO](https://github.com/lasgroup/SDPO), checked out next to this repository.
4. **Evaluation.** A sweep spec expands to cells (game × value × presentation × opponent × checkpoint), packed four per node on SLURM. Each cell records behaviour and two teacher-forcing probes.
5. **Analysis.** Results markdown, figures, and tables are regenerated from the cell data without GPUs.

## Setup

Developed on the CSCS Alps GH200 system: SLURM, NVIDIA GH200 (aarch64) nodes with four GPUs each, and container environments through EDF. Training and evaluation run inside the container; dataset generation, analysis and tests do not.

**1. Check out both repositories side by side.**

```bash
git clone https://github.com/lasgroup/SDPO.git ~/SDPO             # verl fork with SDPO
git clone <this repository> ~/MoralGymVerl
```

**2. Fill in the cluster settings.** Everything site-specific lives in one git-ignored file.

```bash
cd ~/MoralGymVerl
cp scripts/slurm/cluster.env.example scripts/slurm/cluster.env
$EDITOR scripts/slurm/cluster.env
```

`SLURM_ACCOUNT` is the account charged for jobs, `MORALGYM_CKPT_ROOT` a large scratch path for checkpoints and rollouts (it must be visible inside the container), `MORALGYM_STORE_ROOT` an optional long-term copy, and `MORALGYM_DIR` / `SDPO_DIR` the two checkouts. `HF_HOME` is the Hugging Face cache. If your scheduler needs an account on every submission, also `export SBATCH_ACCOUNT=<account>` in your shell profile.

**3. Build the container.**

```bash
bash scripts/setup/setup_verl.sh
```

This builds `Dockerfile.gh200` with podman (NGC vLLM, verl through SDPO, Python 3.11), exports it as a squashfs image to `$SCRATCH/containers/`, and writes the EDF file `~/.edf/moralgym_verl.toml`, which bind-mounts your home and scratch and pins `PYTHONPATH`, the HF cache, and the FlashInfer attention backend that Gemma-2 needs. Rebuilding means deleting the `.sqsh` file and rerunning; the two packages are reinstalled from the bind-mounted sources at job start, so code changes need no rebuild.

**4. Fetch the model.** The container runs offline, so weights must already be in the cache.

```bash
HF_HOME=<your HF_HOME> hf download Qwen/Qwen3-8B
```

Gated models need `hf auth login` first. For models above roughly 10 GB, run the download in a transfer job rather than on the login node.

**5. Check that a run resolves, then train.**

```bash
DRY_RUN=1 bash scripts/slurm/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200   # prints the resolved job, submits nothing
bash scripts/slurm/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200
```

For a new model, check the chat template first with `scripts/preflight/check_model_template.py`, and run a short debug job before the full one. Checkpoints, rollouts and logs land under `MORALGYM_CKPT_ROOT/<run>`; training logs go to `~/logs_verl/training`.

**6. Evaluate and analyse.**

```bash
python3.11 scripts/slurm/submit_sweep.py configs/eval/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder.yaml
python3.11 scripts/analysis/make_results.py eval_results/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder
```

The sweep submits one packed job per four cells and writes each cell under `eval_results/`, mirroring the spec's path. The analysis step turns those cells into markdown, figures and tables.

**Tests** run without GPUs or the container:

```bash
PYTHONPATH=src:scripts/analysis python3.11 -m pytest tests/
```

## Naming

One grammar names a run, and every path keys off it:

```
<model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>      e.g. qwen3_8b_grpo_pd_deon_tft_200
configs/training/<run>.yaml                                one file per run
configs/eval/<root>/<subject>/<family>/<experiment>.yaml   mirrored by eval_results/<root>/<subject>/<family>/<experiment>/
```

`<root>` is `teacher_signal` (base-model screens), `post_training` (trained checkpoints), or `transfer` (held-out games); `<family>` is `classic` (2×2 games) or `pgg`. A sweep spec's path is its identity, so specs do not declare a name. The launchers and `scripts/audit_naming.py` enforce the contract.

## Layout

```
configs/            training/ (one YAML per run), datasets/, eval/ (sweep specs; path = identity)
src/moralgym_verl/
  game/             games, opponents, prompts, moral-value wordings
  training/         dataset generation, verl reward function, SDPO feedback
  eval/             behavioural eval, teacher-forcing probes, sweep expansion
  rewards.py        game and intrinsic reward variants
scripts/            slurm/ launchers · analysis/ results and figures · preflight/ new-model checks · setup/
tests/
```

Evaluation writes its cell data to `eval_results/`, which is not tracked. Some comments cite design notes under `docs/`; those notes are unpolished working material and are not part of this repository.

## Games, opponents, values

| | |
|---|---|
| Games | `prisoners_dilemma`, `stag_hunt`, `chicken` (2×2); `public_goods` (n-player) |
| Opponents | `tit_for_tat`, `always_defect`, `always_cooperate`, `random`, `noisy_tft`, `grim_trigger`, `pavlov`; PGG: `conditional_contributor`, `noisy_conditional`, `free_rider`, `full_contributor`, `random_contributor` |
| Values | `deontological`, `utilitarian`, `virtue`, `universalization`, `forgiveness`, `repair`, `generosity`, `exploit_resistance`, plus wording variants |
| Models | Qwen3 4B/8B/32B, Gemma-2 9B, Gemma-3 12B, Llama-3.1 8B |

## Requirements

- NVIDIA GH200 (aarch64) container built from `Dockerfile.gh200`: NGC vLLM 25.12, verl through SDPO, Python 3.11
- Analysis without GPUs: Python 3.11 with numpy and matplotlib
- Hugging Face access for gated models; Weights & Biases optional

## License

Apache 2.0, see [LICENSE](LICENSE).

## Acknowledgements

Built on [verl](https://github.com/volcengine/verl) and [SDPO](https://github.com/lasgroup/SDPO) (Apache 2.0). Reward design follows Tennant et al., *Moral Alignment for LLM Agents*.
