# Naming contract

Adopted 2026-08-31. One grammar names everything; paths mirror each other so
config ↔ job ↔ results is always a rename-free lookup, and the submit scripts
*enforce* the contract rather than trusting it (`train_verl.sh` step 1,
`moralgym_verl.eval.sweep.load_sweep`).

## Grammar

- `_` separates fields; `-` joins words inside a field; `__` only between
  eval cell axes (`run_dir_stem`).
- Token registry (extend here first, then `MODEL_TOKENS` in `eval/sweep.py`):
  - models: `gemma2_9b`, `gemma3_12b`, `llama31_8b`, `qwen3_8b`, `qwen3_32b`
  - algos: `grpo`, `sdpo` · games: `pd`, `pgg`, `chicken`
  - game families (`GAME_FAMILIES`, mirrors `src/moralgym_verl/game/`):
    `classic` = 2x2 matrix games (pd, stag_hunt, chicken) · `pgg` = n-player
  - arms: `none`, `deon`, `util`, `deon-repair-gen`, … · opponents: `tft`, `random`, …

**The primary key is the training run name:**

```
<model>_<size>_<algo>_<game>_<arm>_<opponent>_<steps>[_vN]
e.g. qwen3_8b_grpo_pd_util_tft_150
```

## Layout — one name, everything keys off it

```
configs/
├─ training/                 1 launchable file = 1 run, named <RUN_NAME>.yaml
│  ├─ _base_clariden.yaml    cluster paths (was moralgym_user.yaml)
│  ├─ _base_grpo_pd.yaml     shared reward-agnostic trainers; _-prefixed =
│  └─ _base_sdpo_pd.yaml     not launchable
├─ datasets/                 arm identity -> training parquet:
│  └─ <algo>_<game>_<arm>_<opponent>[_mt].yaml
└─ eval/                     1 file = 1 experiment; PATH = identity
   ├─ harness/<model>/<family>.yaml       measurement profile: model x game
   │                                      family (HF id, generation budget,
   │                                      prompt regime, presentation). No
   │                                      experiment content; every sweep of
   │                                      that model resolves to it, so base
   │                                      rows and checkpoint rows share one
   │                                      protocol.
   ├─ teacher_signal/<model>/<family>/<experiment>.yaml   base-model screens
   └─ post_training/<RUN_NAME>/<family>/<experiment>.yaml  one run, its
      │                                   training game (ckpt_ladder)
      └─ post_training/<model>/<family>/<experiment>.yaml  model level:
                                          held-out games (transfer), or
                                          several runs of the model side by
                                          side; checkpoints from any run of
                                          that model; base cells live here

eval_results/                mirrors configs/eval/ path-for-path
   (+ _debug/, _archive/ — underscore dirs are outside the contract)
```

Derived from `RUN_NAME` automatically (`train_verl.sh`): the dataset config
(fields 3–6), W&B run, `$SCRATCH/moralgym_verl_runs/<RUN_NAME>` and the store
copy, `logs_verl/training/<jobid>_<RUN_NAME>.log`. Derived from the sweep
path (`load_sweep`): results dir, manifest location, default model profile;
`eval_group`/`results_dir`/`name` keys in a sweep are refused, a sweep's
`game:` axis must stay inside its family dir, and a post_training sweep's
checkpoints must belong to its subject run (a model-level sweep's to a run
of that model). Game-specific experiments under
`classic/` keep a game prefix (`pd_*`) since the family spans three games.

Submit forms:

```
DRY_RUN=1 bash scripts/slurm/train_verl.sh <RUN_NAME>          # resolve only
bash scripts/slurm/train_verl.sh <RUN_NAME>                    # submit
/usr/bin/python3.11 scripts/slurm/submit_sweep.py \
    configs/eval/<root>/<subject>/<experiment>.yaml --dry-run
```

## Legacy table (pre-2026-08-31 names)

Old checkpoint-dir names survive as symlinks on scratch + store. W&B runs,
HF adapter repos, old manifests and the $STORE eval_results mirror keep the
old spellings — read them through this table.

Training runs (checkpoints, W&B, logs):

| old | canonical |
|---|---|
| gemma_run1_70 / qwen_run1_70 | {gemma2_9b,qwen3_8b}_sdpo_pd_deon-repair-gen_tft_70 |
| gemma_run2_200 / qwen_run2_200 | {gemma2_9b,qwen3_8b}_sdpo_pd_deon-repair-gen_tft_200 |
| grpo_deon_tft_200 | qwen3_8b_grpo_pd_deon_tft_200 |
| grpo_util_tft_150 | qwen3_8b_grpo_pd_util_tft_150 |
| llama31_deon_150_v2 | llama31_8b_grpo_pd_deon_tft_150 (job 3204538; the first attempt llama31_deon_150, job 3203600, had a verl tool-call preamble in every prompt and was deleted 2026-09-03) |

Trainer configs: sdpo_run1_pd_{gemma,qwen3} → the `_tft_70` run files;
sdpo_run2_pd_{gemma,qwen3,qwen3_32b} → the `_tft_200` run files;
grpo_deon_pd_qwen3 → qwen3_8b_grpo_pd_deon_tft_200; grpo_pd_qwen3_8b →
qwen3_8b_grpo_pd_util_tft_150; grpo_pd_{llama31_8b,gemma3_12b} → the deon
150 run files; moralgym_user/grpo_pd_base/sdpo_run1_pd → `_base_*`.

Datasets: grpo_pd_tft → grpo_pd_none_tft; grpo_deon_pd_tft → grpo_pd_deon_tft;
grpo_deon_pd → grpo_pd_deon_random; grpo_util_pd_tft → grpo_pd_util_tft;
sdpo_run1_pd → sdpo_pd_deon-repair-gen_tft.

Eval profiles (now `configs/eval/harness/<model>/<family>.yaml`): teacher_signal_9b →
harness/gemma2_9b/classic.yaml; teacher_signal_qwen3_8b →
qwen3_8b/classic/; teacher_signal_qwen3_32b → qwen3_32b/classic/;
pgg_screen_9b → gemma2_9b/pgg/; pgg_screen_qwen3_8b → qwen3_8b/pgg/.

Sweeps → results (both trees moved identically):

| old sweep → old results dir | new path under both trees |
|---|---|
| single_turn_screen[_qwen3] → single_turn_screen_{gemma-2-9b-it,qwen3-8b} | teacher_signal/{gemma2_9b,qwen3_8b}/classic/single_turn_screen |
| generosity_arm[_qwen3] → generosity_arm_* | teacher_signal/{gemma2_9b,qwen3_8b}/classic/pd_generosity_arm |
| pd_presentation_robustness[_qwen3] | teacher_signal/{gemma2_9b,qwen3_8b}/classic/pd_presentation_robustness |
| pd_randomization_ablation | teacher_signal/gemma2_9b/classic/pd_randomization_ablation |
| pgg_single_turn → pgg_single_turn_gemma-2-9b-it | teacher_signal/gemma2_9b/pgg/single_turn |
| pgg_single_turn_qwen3_v2–v4 → pgg_single_turn_qwen3-8b[_v2,_v3] | teacher_signal/qwen3_8b/pgg/single_turn[_v2,_v3,_v4] |
| ckpt_gemma_run2_200 → gemma-2-9b-pd-sdpo-deon-repair-gen | post_training/gemma2_9b_sdpo_pd_deon-repair-gen_tft_200/classic/ckpt_ladder |
| ckpt_qwen_run2_200 → qwen3-8b-pd-sdpo-deon-repair-gen | post_training/qwen3_8b_sdpo_pd_deon-repair-gen_tft_200/classic/ckpt_ladder |
| ckpt_grpo_deon_tft_200 → qwen3-8b-pd-grpo-deon-tft | post_training/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder |
| ckpt_pgg_transfer_qwen3 → qwen3-8b-pgg-transfer | post_training/qwen3_8b/pgg/transfer |

Nothing is deferred: the one job queued mid-migration (3243465, the v4
deon-wording screen) was cancelled before it ran and resubmitted under the
contract path, its vestigial old-path dirs deleted, and the transition
symlinks removed with it. Old TRAINING names still resolve via the
checkpoint-dir symlinks on scratch and store.
