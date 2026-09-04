#!/bin/bash
# Regenerate every figure, table, and panel of the results report and
# mirror them into the Overleaf clone (~/moralgym-report). This file is
# the single record of what the report shows — runs, labels, and the
# curated trace picks. Login node, no container.
#
#   bash scripts/analysis/make_report.sh
#   cd ~/moralgym-report && git add -A && git commit -m "regen" && git push
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=/usr/bin/python3.11
MF="scripts/analysis/make_figures.py"

RUNS=~/logs_verl/runs
PT=eval_results/post_training
SCREEN_QWEN=eval_results/teacher_signal/qwen3_8b/classic/single_turn_screen
SCREEN_GEMMA=eval_results/teacher_signal/gemma2_9b/classic/single_turn_screen

GRPO_QWEN=$PT/qwen3_8b_grpo_pd_deon_tft_200/classic/ckpt_ladder
SDPO_QWEN=$PT/qwen3_8b_sdpo_pd_deon-repair-gen_tft_200/classic/ckpt_ladder
SDPO_GEMMA=$PT/gemma2_9b_sdpo_pd_deon-repair-gen_tft_200/classic/ckpt_ladder

$PY $MF training-grid --name grpo --window 5 \
    --run "Qwen3-8B GRPO=$RUNS/qwen3_8b_grpo_pd_deon_tft_200" \
    --run "Gemma3-12B GRPO=$RUNS/gemma3_12b_grpo_pd_deon_tft_150" \
    --run "Llama3.1-8B GRPO=$RUNS/llama31_8b_grpo_pd_deon_tft_150_v2"

$PY $MF training-grid --name sdpo --window 5 \
    --run "Qwen3-8B SDPO=$RUNS/qwen3_8b_sdpo_pd_deon-repair-gen_tft_200" \
    --run "Gemma2-9B SDPO=$RUNS/gemma2_9b_sdpo_pd_deon-repair-gen_tft_200"

$PY $MF ladder-grid --name grpo --macro-prefix GDT \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --reference $SCREEN_QWEN

$PY $MF ladder-grid --name sdpo \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --reference $SCREEN_QWEN --reference $SCREEN_GEMMA

$PY $MF dopp-compare \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --reference $SCREEN_QWEN --reference $SCREEN_GEMMA

# Held-out-game transfer (eval_results/transfer/): pooled, last-checkpoint,
# every-checkpoint figures across all runs; in-context row from the Qwen v3 screen.
$PY $MF transfer-grid --name pgg --principle deontological \
    --group eval_results/transfer/qwen3_8b/pgg/single_round \
    --group eval_results/transfer/gemma2_9b/pgg/single_round \
    --group eval_results/transfer/gemma3_12b/pgg/single_round \
    --reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3

$PY $MF trace-table \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --reference $SCREEN_QWEN --reference $SCREEN_GEMMA

$PY $MF prompt-panels --ladder $GRPO_QWEN

$PY $MF prompt-panels --name pgg --game-only \
    --ladder eval_results/transfer/qwen3_8b/pgg/single_round \
    --analysis-dir eval_results/post_training/comparison/analysis

# Curated trace exemplars (within a pick, selection is the same seeded
# rule as the traces_checkpoints_*.md docs):
#   GRPO before/after its training jump in the repair state D_A C_O;
#   SDPO in the betrayed state C_A D_O: Qwen recites, Gemma paraphrases.
$PY $MF trace-panels \
    --pick "Qwen3-8B GRPO=$GRPO_QWEN:60:DC" \
    --pick "Qwen3-8B GRPO=$GRPO_QWEN:180:DC" \
    --pick "Qwen3-8B SDPO=$SDPO_QWEN:200:CD" \
    --pick "Gemma2-9B SDPO=$SDPO_GEMMA:200:CD"

echo "report regenerated; commit + push ~/moralgym-report to publish"
