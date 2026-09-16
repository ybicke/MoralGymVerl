# CoopGym: Fine-Tuning for Cooperation in Multi-Agent Systems

Training language-model agents to cooperate in multi-agent systems through reasoning post-training: online RL with RLVR (GRPO) and RLRF self-distillation (SDPO) in [verl](https://github.com/volcengine/verl). Moral principles are the first training signal, and game-theoretic games the first environments.

## Overview

LLM agents increasingly negotiate, coordinate, and compete with agents whose goals only partly align with their own. In such mixed-motive settings they need to sustain cooperation without becoming exploitable, so that the agent population does not collapse and agents do not collude against their environment. This project trains that behaviour with online RL on games, where equilibria are known and cooperation has a precise meaning.

The first approach delivers a moral principle, such as *do not defect on a cooperator*, through one of two algorithms on the same environment:

- **GRPO (reward).** Game payoff plus a weighted intrinsic term derived from the principle.
- **SDPO (teacher).** The principle is given in a teacher context, and the student distills the teacher's next-token distribution. A plain-language principle carries over to other games more easily than a hand-crafted reward, and per-token credit assignment should help multi-turn training.

Training so far is single-step: one Prisoner's Dilemma decision against tit-for-tat, conditioned on a fabricated previous round. Evaluation uses multi-round play against scripted opponents and a held-out n-player Public Goods Game, and asks whether the principle became a disposition: conditional, robust to prompt presentation, visible in the reasoning, and transferable.

## Research questions

1. **Cooperation from games.** Can training on games make an LLM a cooperative agent?
2. **Generalisation.** Does learned cooperation transfer to unseen environments?
3. **Moral principles as the signal.** Can cooperation be taught from moral principles, as implicit reward or as plain-text teaching feedback?
4. **Safety.** How can cooperation stay safe, so that agents do not collude and the system does not collapse?
5. **Beyond single-step games.** Can the approach extend to learning co-players, long-horizon tasks, and continual learning across games?

## Status

A first exploratory study with Qwen3-8B, partly replicated on Gemma and Llama. Both GRPO and SDPO learn reciprocity in the Prisoner's Dilemma. GRPO's reasoning shows no moral language; SDPO's does, and its cooperation is more forgiving but more exploitable. In a first transfer test on a four-player Public Goods Game, SDPO's cooperation carries over and GRPO's does not. Matrix games are likely too narrow for broad transfer, so richer environments come next.

## How it works

1. **Dataset.** `moralgym_verl.training.dataset` writes verl-format parquet prompts at submit time, with the game state as ground truth.
2. **Rollout and reward.** `reward_fn` parses the move and returns payoff plus intrinsic reward, and for SDPO the teacher feedback (a moral principle or an outcome critique).
3. **Update.** GRPO or SDPO in the verl fork [lasgroup/SDPO](https://github.com/lasgroup/SDPO).
4. **Evaluation.** A sweep spec expands to cells (game × value × presentation × opponent × checkpoint), packed four per node on SLURM. Each cell records behaviour and two teacher-forcing probes.
5. **Analysis.** Markdown, figures, and tables are regenerated from the cells without GPUs.

## Setup

Developed on CSCS Alps: GH200 (aarch64) nodes, SLURM, and EDF container environments. Training and evaluation run in the container; everything else runs without it.

```bash
# 1. Check out both repositories
git clone https://github.com/lasgroup/SDPO.git ~/SDPO
git clone https://github.com/ybicke/coopGym.git ~/coopGym && cd ~/coopGym

# 2. Cluster settings: account, checkpoint root, optional long-term store, HF cache
cp scripts/slurm/cluster.env.example scripts/slurm/cluster.env && $EDITOR scripts/slurm/cluster.env

# 3. Container: podman build, squashfs export, EDF file ~/.edf/moralgym_verl.toml
bash scripts/setup/setup_verl.sh

# 4. Model weights (the container runs offline)
HF_HOME=<your HF_HOME> hf download Qwen/Qwen3-8B

# 5. Train: resolve first, then submit
DRY_RUN=1 bash scripts/slurm/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200
bash scripts/slurm/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200

# 6. Evaluate and analyse
python3.11 scripts/slurm/submit_sweep.py configs/eval/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder.yaml
python3.11 scripts/analysis/make_results.py eval_results/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder

# Tests (no GPU, no container)
PYTHONPATH=src:scripts/analysis python3.11 -m pytest tests/
```

- `cluster.env` is git-ignored. `SLURM_ACCOUNT` and `MORALGYM_CKPT_ROOT` (scratch, visible inside the container) are required; `MORALGYM_STORE_ROOT` adds a long-term copy of checkpoints. If your scheduler needs an account on every job, also export `SBATCH_ACCOUNT`.
- Code changes need no rebuild: both packages are reinstalled from the mounted sources at job start.
- For a new model, run `scripts/preflight/check_model_template.py` and a short debug job before the full run. Gated models need `hf auth login`; download models above ~10 GB in a transfer job.
- Checkpoints and rollouts land in `MORALGYM_CKPT_ROOT/<run>`, logs in `~/logs_verl/`, and evaluation cells in `eval_results/`, mirroring the spec path.

## Naming

One grammar names a run, and every path keys off it:

```
<model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>      e.g. qwen3_8b_grpo_pd_deon_tft_200
configs/training/<run>.yaml                                one file per run
configs/eval/<root>/<subject>/<family>/<experiment>.yaml   mirrored by eval_results/<root>/<subject>/<family>/<experiment>/
```

`<root>` is `teacher_signal` (base-model screens), `post_training` (trained checkpoints), or `transfer` (held-out games); `<family>` is `classic` (2×2 games) or `pgg`. A sweep spec's path is its identity. The launchers and `scripts/audit_naming.py` enforce the contract.

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

The Python package is still named `moralgym_verl`, after the project's first phase.

## Games, opponents, values

| | |
|---|---|
| Games | `prisoners_dilemma`, `stag_hunt`, `chicken` (2×2); `public_goods` (n-player) |
| Opponents | `tit_for_tat`, `always_defect`, `always_cooperate`, `random`, `noisy_tft`, `grim_trigger`, `pavlov`; PGG: `conditional_contributor`, `noisy_conditional`, `free_rider`, `full_contributor`, `random_contributor` |
| Values | `deontological`, `utilitarian`, `virtue`, `universalization`, `forgiveness`, `repair`, `generosity`, `exploit_resistance`, plus wording variants |
| Models | Qwen3 4B/8B/32B, Gemma-2 9B, Gemma-3 12B, Llama-3.1 8B |

## License

Apache 2.0, see [LICENSE](LICENSE). Built on [verl](https://github.com/volcengine/verl) and [SDPO](https://github.com/lasgroup/SDPO) (Apache 2.0). Reward design follows Tennant et al., *Moral Alignment for LLM Agents*.
