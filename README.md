# MoralGymVerl

Training language-model agents to cooperate in multi-agent systems, by fine-tuning on games. GRPO and self-distillation (SDPO) on top of [verl](https://github.com/volcengine/verl).

## Overview

LLM agents increasingly act in multi-agent systems, negotiating, coordinating, and competing with other agents whose goals only partly align with their own. Cooperation in such mixed-motive settings is then a training target rather than a hope. This project trains it with reinforcement learning on games, and asks what the trained agent carries with it into new multi-agent interactions.

Game-theoretic games are the first testbed: an LLM plays repeated matrix games (Prisoner's Dilemma, Stag Hunt, Chicken) and an n-player Public Goods Game against scripted opponents. They are small, their equilibria are known, and cooperation in them has a precise meaning. The environment is designed to admit richer games later, from negotiation to multi-agent coordination.

A moral principle such as *do not defect on a cooperator* is delivered through one of two training channels that share the same environment:

- **Reward (GRPO).** Per-round reward is game payoff plus a weighted intrinsic term that scores the move against the principle.
- **Teacher (SDPO).** The principle is written into a teacher context; the student distills the teacher's next-token distribution on its own rollouts.

Evaluation asks whether the principle became a disposition: does trained behaviour track it conditionally, survive changes in prompt presentation, show up in the model's reasoning, and carry over to held-out games and multi-round play?

## Research questions

1. **Cooperation from games.** Can training on games make an LLM a cooperative agent in a multi-agent system?
2. **Generalisation.** Does cooperative behaviour learned in one game transfer to unseen environments?
3. **Moral principles as the signal.** Can cooperation in mixed-motive games, social dilemmas, and other games be taught from moral principles, delivered as reward or as text?
4. **Safety.** How can cooperative behaviour in multi-agent systems be made safe, so that it does not collapse into exploitability or collusion?

## How it works

1. **Dataset.** `moralgym_verl.training.dataset` samples episodes and writes verl-format parquet prompts; the game state travels as the ground truth.
2. **Rollout and reward.** verl generates a response per prompt. `reward_fn` parses the move, returns payoff plus intrinsic reward, and for SDPO a textual moral critique as feedback.
3. **Update.** GRPO or SDPO in the vendored verl fork ([lasgroup/SDPO](https://github.com/lasgroup/SDPO)).
4. **Evaluation.** A sweep spec expands to cells (game × value × presentation × opponent × checkpoint), packed four per node on SLURM. Each cell records behaviour and two teacher-forcing probes.
5. **Analysis.** Results markdown, figures, and report panels are regenerated from the cell data on the login node.

## Quick start

On Clariden (GH200). Training and evaluation run inside the container; analysis and tests do not need it.

```bash
bash scripts/setup/setup_verl.sh                                   # build container, write EDF config
bash scripts/slurm/train_verl.sh qwen3_8b_grpo_pd_deon_tft_200      # train (add DRY_RUN=1 to resolve only)
python3.11 scripts/slurm/submit_sweep.py configs/eval/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder.yaml
python3.11 scripts/analysis/make_results.py eval_results/post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder
bash scripts/analysis/make_report.sh                               # every figure and table of the report
python3 -m pytest tests/                                           # inside the container
```

## Naming

One grammar names a run, and every path keys off it:

```
<model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>      e.g. qwen3_8b_grpo_pd_deon_tft_200
configs/training/<run>.yaml                                one file per run
configs/eval/<root>/<subject>/<family>/<experiment>.yaml   mirrored by eval_results/<root>/<subject>/<family>/<experiment>/
```

The launchers enforce the contract. Details in [docs/naming.md](docs/naming.md).

## Layout

```
configs/            training/ (one YAML per run), datasets/, eval/ (sweep specs; path = identity)
src/moralgym_verl/
  game/             games, opponents, prompts, moral-value wordings
  training/         dataset generation, verl reward function, SDPO feedback
  eval/             behavioural eval, teacher-forcing probes, sweep expansion
  rewards.py        game and intrinsic reward variants
scripts/            slurm/ launchers · analysis/ results and figures · preflight/ new-model checks · debug/
eval_results/       analysis markdown per experiment (cell data stays out of git)
docs/               design notes and results
tests/
```

## Games, opponents, values

| | |
|---|---|
| Games | `prisoners_dilemma`, `stag_hunt`, `chicken` (2×2); `public_goods` (n-player) |
| Opponents | `tit_for_tat`, `always_defect`, `always_cooperate`, `random`, `noisy_tft`, `grim_trigger`, `pavlov`; PGG: `conditional_contributor`, `noisy_conditional`, `free_rider`, `full_contributor`, `random_contributor` |
| Values | `deontological`, `utilitarian`, `virtue`, `universalization`, `forgiveness`, `repair`, `generosity`, `exploit_resistance`, plus wording variants |
| Models | Qwen3 4B/8B/32B, Gemma-2 9B, Gemma-3 12B, Llama-3.1 8B |

## Documentation

- [naming.md](docs/naming.md): the naming contract and directory mirror
- [sdpo_design_analysis.md](docs/sdpo_design_analysis.md): why SDPO, and the experimental design
- [teacher_signal_eval.md](docs/teacher_signal_eval.md): pre-training screen of the teacher signal
- [post_training_comparison.md](docs/post_training_comparison.md): SDPO vs GRPO after training
- [pgg_design.md](docs/pgg_design.md): the Public Goods Game and transfer
- [multi_turn.md](docs/multi_turn.md): multi-round execution and update schemes
- [verl_local_patches.md](docs/verl_local_patches.md): deviations from upstream verl

## Requirements

- NVIDIA GH200 (aarch64) container built from `Dockerfile.gh200`: NGC vLLM 25.12, verl via SDPO, Python 3.11
- Login-node analysis: Python 3.11 with numpy and matplotlib
- Hugging Face access for gated models; Weights & Biases optional

## Acknowledgements

Built on [verl](https://github.com/volcengine/verl) and [SDPO](https://github.com/lasgroup/SDPO) (Apache 2.0). Reward design follows Tennant et al., *Moral Alignment for LLM Agents*.
