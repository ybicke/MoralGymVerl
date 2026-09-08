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
    --run "Qwen3-8B GRPO util=$RUNS/qwen3_8b_grpo_pd_util_tft_150"

$PY $MF training-grid --name sdpo --window 5 \
    --run "Qwen3-8B SDPO=$RUNS/qwen3_8b_sdpo_pd_deon-repair-gen_tft_200" \
    --run "Gemma2-9B SDPO=$RUNS/gemma2_9b_sdpo_pd_deon-repair-gen_tft_200"

$PY $MF ladder-grid --name grpo --macro-prefix GDT \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --reference $SCREEN_QWEN

$PY $MF ladder-grid --name sdpo \
    --reference eval_results/teacher_signal/qwen3_8b/classic/pd_generosity_arm \
    --reference eval_results/teacher_signal/gemma2_9b/classic/pd_generosity_arm \
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
    --group eval_results/transfer/qwen3_8b/pgg/single_round_util \
    --group eval_results/transfer/gemma2_9b/pgg/single_round \
    --group eval_results/transfer/gemma3_12b/pgg/single_round \
    --reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3

$PY $MF trace-table --steps 0,60,90,120,final \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --transfer "Qwen3-8B GRPO=eval_results/transfer/qwen3_8b/pgg/single_round" \
    --transfer "Qwen3-8B SDPO=eval_results/transfer/qwen3_8b/pgg/single_round" \
    --transfer "Gemma2-9B SDPO=eval_results/transfer/gemma2_9b/pgg/single_round" \
    --transfer "Gemma3-12B GRPO=eval_results/transfer/gemma3_12b/pgg/single_round" \
    --pgg-reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3 \
    --reference $SCREEN_QWEN --reference $SCREEN_GEMMA

$PY $MF prompt-panels --ladder $GRPO_QWEN

$PY $MF prompt-panels --name pgg --game-only \
    --ladder eval_results/transfer/qwen3_8b/pgg/single_round \
    --analysis-dir eval_results/post_training/comparison/analysis

# Curated trace exemplars (within a pick, selection is the same seeded
# rule as the traces_checkpoints_*.md docs):
#   GRPO before/after its training jump in the repair state D_A C_O;
#   SDPO in the betrayed state C_A D_O: Qwen recites, Gemma paraphrases.
# Marks: red (fail=) = the flawed inference, violet (recite=) = verbatim
# recitation of the teacher wording.
$PY $MF trace-panels \
    --pick "Qwen3-8B GRPO=$GRPO_QWEN:60:DC" \
    --mark "" --note "" \
    --pick "Qwen3-8B GRPO=$GRPO_QWEN:180:DC" \
    --mark "fail=**choosing action3** is more likely to result in a better outcome" \
    --note "wrong: its own table two sentences above gives defecting 4 > 3 against action3" \
    --pick "Qwen3-8B SDPO=$SDPO_QWEN:200:CD" \
    --mark "recite=If you have taken advantage of others who acted in good faith, stop and return to acting in good faith" \
    --note "verbatim quote of the teacher principle, which is not in this prompt" \
    --pick "Gemma2-9B SDPO=$SDPO_GEMMA:200:CD" \
    --mark "fail=A acted in good faith last round by choosing action4" \
    --note "misreads the state: action4 was the opponent's defection, then cited to justify defecting back" \
    --pick "Qwen3-8B SDPO=$SDPO_QWEN:200:max-recite" \
    --mark "" \
    --note "the principle's opening clause, 21 words one-to-one" \
    --pick "Qwen3-8B SDPO=$SDPO_QWEN:120:CD" \
    --mark "norm=encourage A to act in good faith in future rounds" \
    --note "the norm in the model's own words: normative 100 percent at this step, verbatim recitation only 12 percent"

# Multi-round (in-play) documents + figures: one spec for both roots
# (specs/multi_round.py); figures mirrored by hand since make_results
# has no report hook.
$PY scripts/analysis/make_results.py $PT/qwen3_8b/classic/multi_round \
    --reference $GRPO_QWEN --reference $SDPO_QWEN
$PY scripts/analysis/make_results.py eval_results/transfer/qwen3_8b/pgg/multi_round \
    --reference eval_results/transfer/qwen3_8b/pgg/single_round
for f in $PT/qwen3_8b/classic/multi_round/analysis/figures/pdf/multi_round_*.pdf \
         eval_results/transfer/qwen3_8b/pgg/multi_round/analysis/figures/pdf/multi_round_*.pdf; do
    cp "$f" ~/moralgym-report/figures/ && echo "mirrored $f"
done

echo "report regenerated; commit + push ~/moralgym-report to publish"
