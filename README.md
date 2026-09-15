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

A first exploratory study compares GRPO and SDPO on the single-step Prisoner's Dilemma with Qwen3-8B (partial replication on Gemma and Llama). Both learn reciprocity. GRPO's reasoning shows no moral language, while SDPO's does and its cooperation is more forgiving but more exploitable. In a first transfer test on a four-player Public Goods Game, SDPO's cooperation carries over and GRPO's does not. Matrix games are likely too narrow for broad transfer. Details are in [post_training_comparison.md](docs/post_training_comparison.md) and the intermediate report built by `scripts/analysis/make_report.sh`.

## How it works

1. **Dataset.** `moralgym_verl.training.dataset` samples episodes and writes verl-format parquet prompts; the game state travels as the ground truth.
2. **Rollout and reward.** verl generates a response per prompt. `reward_fn` parses the move, returns payoff plus intrinsic reward, and for SDPO the teacher feedback: either an outcome critique or a fixed moral principle (the reported runs use the principle).
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
- [multi_turn_current_state_and_flaws.md](docs/multi_turn_current_state_and_flaws.md): where multi-turn training stands
- [game_expansion_design.md](docs/game_expansion_design.md): richer games beyond the 2×2 dilemmas
- [literature_multiagent_environments_benchmarks.md](docs/literature_multiagent_environments_benchmarks.md): survey of multi-agent environments and benchmarks
- [verl_local_patches.md](docs/verl_local_patches.md): deviations from upstream verl

## Requirements

- NVIDIA GH200 (aarch64) container built from `Dockerfile.gh200`: NGC vLLM 25.12, verl via SDPO, Python 3.11
- Login-node analysis: Python 3.11 with numpy and matplotlib
- Hugging Face access for gated models; Weights & Biases optional

## Acknowledgements

Built on [verl](https://github.com/volcengine/verl) and [SDPO](https://github.com/lasgroup/SDPO) (Apache 2.0). Reward design follows Tennant et al., *Moral Alignment for LLM Agents*.
