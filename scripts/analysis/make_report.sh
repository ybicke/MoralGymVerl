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
PY=${PY:-/usr/bin/python3.11}      # Fir: PY=~/venvs/moralgym/bin/python
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

$PY $MF ladder-grid --name all --ncols 3 --macro-prefix GDT \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --reference $SCREEN_QWEN --reference $SCREEN_GEMMA \
    --reference eval_results/teacher_signal/qwen3_8b/classic/pd_generosity_arm \
    --reference eval_results/teacher_signal/gemma2_9b/classic/pd_generosity_arm

$PY $MF dopp-compare \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --reference $SCREEN_QWEN --reference $SCREEN_GEMMA

# Held-out-game transfer (eval_results/transfer/): pooled, last-checkpoint,
# every-checkpoint figures; in-context row from the Qwen v3 screen.
# Main-text figure = Qwen3-8B only (the controlled channel contrast);
# Gemma runs are the cross-model replication and render as the appendix figure.
$PY $MF transfer-grid --name pgg_qwen3_8b --principle deontological \
    --group eval_results/transfer/qwen3_8b/pgg/single_round \
    --group eval_results/transfer/qwen3_8b/pgg/single_round_util \
    --reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3
$PY $MF transfer-grid --name pgg_gemma --principle deontological \
    --group eval_results/transfer/gemma2_9b/pgg/single_round \
    --group eval_results/transfer/gemma3_12b/pgg/single_round \
    --reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3
# Summary table stays whole (all runs, the citable anchor in the chapter).
$PY $MF transfer-grid --table-only --principle deontological \
    --group eval_results/transfer/qwen3_8b/pgg/single_round \
    --group eval_results/transfer/qwen3_8b/pgg/single_round_util \
    --group eval_results/transfer/gemma2_9b/pgg/single_round \
    --group eval_results/transfer/gemma3_12b/pgg/single_round \
    --reference eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3

# Display prompt panel (list representation; the measured protocol is
# prose -- the body caption says so, the prose verbatim panel is in the appendix).
$PY scripts/analysis/render_prompt_panel.py --representation list --state CD \
    --out eval_results/post_training/comparison/analysis/generated/panels/game_prompt_list.txt

# Consolidated hyperparameter table, extracted from the flagship configs.
$PY scripts/analysis/hyperparam_table.py \
    --grpo qwen3_8b_grpo_pd_deon_tft_200 \
    --sdpo qwen3_8b_sdpo_pd_deon-repair-gen_tft_200

# Appendix run ledger (job ids, wall time, W&B) -- derived from the
# training logs; mirrored with the comparison dir by the trace-table call.
$PY scripts/analysis/run_ledger.py \
    --run qwen3_8b_grpo_pd_deon_tft_200 --run qwen3_8b_grpo_pd_util_tft_150 \
    --run qwen3_8b_sdpo_pd_deon-repair-gen_tft_200 \
    --run gemma2_9b_sdpo_pd_deon-repair-gen_tft_200 \
    --run gemma3_12b_grpo_pd_deon_tft_150 --run llama31_8b_grpo_pd_deon_tft_150

# Reasoning-trace length per checkpoint, with the final checkpoint split by
# recitation (appendix table); mirrored with the comparison dir by the
# trace-table call below.
$PY scripts/analysis/trace_length_table.py \
    --ladder "Qwen3-8B GRPO=$GRPO_QWEN" \
    --ladder "Qwen3-8B SDPO=$SDPO_QWEN" \
    --ladder "Gemma2-9B SDPO=$SDPO_GEMMA" \
    --base "Qwen3-8B GRPO=$SCREEN_QWEN/cells/prisoners_dilemma__none__prose__single_round_3072557" \
    --base "Qwen3-8B SDPO=$SCREEN_QWEN/cells/prisoners_dilemma__none__prose__single_round_3072557" \
    --base "Gemma2-9B SDPO=$SCREEN_GEMMA/cells/prisoners_dilemma__none__prose__single_round_3049505" \
    --teacher "Qwen3-8B SDPO=eval_results/teacher_signal/qwen3_8b/classic/pd_generosity_arm/cells/$(basename $(ls -d eval_results/teacher_signal/qwen3_8b/classic/pd_generosity_arm/cells/prisoners_dilemma__deontological+repair+generosity__prose__single_round_* | head -1))" \
    --teacher "Gemma2-9B SDPO=eval_results/teacher_signal/gemma2_9b/classic/pd_generosity_arm/cells/$(basename $(ls -d eval_results/teacher_signal/gemma2_9b/classic/pd_generosity_arm/cells/prisoners_dilemma__deontological+repair+generosity__prose__single_round_* | head -1))"

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
    # reference-gated like mirror_to_report: only figures main.tex names
    grep -q "figures/$(basename "$f" .pdf)" ~/moralgym-report/main.tex || continue
    cp "$f" ~/moralgym-report/figures/ && echo "mirrored $f"
done
cp $PT/qwen3_8b/classic/multi_round/analysis/generated/numbers_MRPD.tex \
   eval_results/transfer/qwen3_8b/pgg/multi_round/analysis/generated/numbers_MRPGG.tex \
   ~/moralgym-report/generated/ && echo "mirrored in-play number macros"

# Paper seed grid (Qwen3-8B, step 200, 3 seeds): PGG transfer macros
# (\Seeds...) + table, the same cells as the seed-grid / seed-multi figures.
# Live play is read from the 200-episode groups (2026-11-11) only: *_n200
# holds base and every seed, *_incontext_n200 the prompted model. Cold
# opening -> \Seeds... / seed_transfer, fabricated opening -> \SeedsFab... /
# seed_transfer_fabopen; Olmo-3 (3 seeds; *_seeds23_* hold seeds 2 and 3) -> \Olmo... / \OlmoFab...
# Olmo-3 is read from the *_tok4096 groups (4096 new tokens per answer): at
# the 2048 of the Qwen cells, up to 19% of its first-round answers are cut
# off before the action line (SDPO, cold opening); Qwen3-8B has none.
T=eval_results/transfer/qwen3_8b/pgg
O=eval_results/transfer/olmo3_7b/pgg
FAB="opened with a fabricated previous round"
QSINGLE="--single-group $T/single_round_sdpo_seed1 --single-group $T/single_round_sdpo_seed2 \
    --single-group $T/single_round_grid --single-group $T/single_round_grpo_v2_seed1 \
    --single-group eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3"
OSINGLE="--single-group $O/single_round_sdpo_seed1 --single-group $O/single_round_grpo_v2_seed1 \
    --single-group $O/single_round_seeds23 \
    --single-group eval_results/teacher_signal/olmo3_7b/pgg/deon_gate"
$PY scripts/analysis/seed_numbers.py --model Qwen3-8B --step 200 $QSINGLE \
    --multi-group $T/multi_round_n200 --multi-group $T/multi_round_incontext_n200 \
    --out $T/analysis
$PY scripts/analysis/seed_numbers.py --model Qwen3-8B --step 200 $QSINGLE \
    --multi-group $T/multi_round_fabopen_n200 --multi-group $T/multi_round_fabopen_incontext_n200 \
    --prefix SeedsFab --table seed_transfer_fabopen --live-play "$FAB" --out $T/analysis
$PY scripts/analysis/seed_numbers.py --model Olmo-3-7B-Instruct --step 200 $OSINGLE \
    --multi-group $O/multi_round_n200_tok4096 --multi-group $O/multi_round_incontext_n200_tok4096 \
    --multi-group $O/multi_round_seeds23_n200_tok4096 \
    --prefix Olmo --table seed_transfer_olmo3_7b --out $O/analysis
$PY scripts/analysis/seed_numbers.py --model Olmo-3-7B-Instruct --step 200 $OSINGLE \
    --multi-group $O/multi_round_fabopen_n200_tok4096 --multi-group $O/multi_round_fabopen_incontext_n200_tok4096 \
    --multi-group $O/multi_round_fabopen_seeds23_n200_tok4096 \
    --prefix OlmoFab --table seed_transfer_olmo3_7b_fabopen --live-play "$FAB" --out $O/analysis
cp $T/analysis/generated/numbers_Seeds{,Fab}.tex $O/analysis/generated/numbers_Olmo{,Fab}.tex \
   ~/moralgym-report/generated/
cp $T/analysis/generated/tables/seed_transfer{,_fabopen}.tex \
   $O/analysis/generated/tables/seed_transfer_olmo3_7b{,_fabopen}.tex ~/moralgym-report/generated/tables/
echo "mirrored seed-grid macros + table"

# ACL live-play figures (acl_main.tex): three-seed Qwen3-8B, the
# random-labels seed, and the Olmo-3 cold vs fabricated opening. Cells
# live in several groups, so they are drawn from --group / --cell lists
# and mirrored by hand (make_figures mirrors only what main.tex names).
CMP=eval_results/transfer/comparison/analysis
# Both openings for both models at 200 episodes (2026-11-11): cold = round 1
# without a previous round, fabopen = round 1 after a fabricated one.
for open in "" _fabopen; do
    $PY $MF seed-multi --group $T/multi_round${open}_n200 --group $T/multi_round${open}_incontext_n200 \
        --step 200 --model Qwen3-8B --principle deontological \
        --name qwen3_8b_seeds${open}_s200 --analysis-dir $CMP
    $PY $MF seed-multi --group $O/multi_round${open}_n200_tok4096 --group $O/multi_round${open}_incontext_n200_tok4096 \
        --group $O/multi_round${open}_seeds23_n200_tok4096 \
        --step 200 --model Olmo-3-7B-Instruct --principle deontological \
        --name olmo3_7b_seeds${open}_s200 --analysis-dir $CMP
done
$PY $MF seed-multi --group $T/multi_round --group $T/multi_round_incontext \
    --group $T/multi_round_sdpo_randlabels_seed1 \
    --step 200 --model Qwen3-8B --principle deontological \
    --name qwen3_8b_sdpo_randlabels_seed1_s200 --analysis-dir $CMP
# Thinking on vs off in live play (ACL thinking appendix): SDPO seeds only,
# each evaluation mode with its own base / +ctx reference.
$PY $MF seed-multi --group $T/multi_round --group $T/multi_round_grid \
    --group $T/multi_round_incontext --group $T/multi_round_sdpo_seed2 \
    --group $T/multi_round_thinking_base --group $T/multi_round_thinking_sdpo \
    --step 200 --model Qwen3-8B --principle deontological \
    --channel SDPO --split-thinking --legend \
    --name qwen3_8b_thinking_vs_nothinking_seeds_s200 --analysis-dir $CMP
# Other models (ACL app:olmo / app:gemma): single-round transfer, one
# panel per model and algorithm. --seed 1 drops Gemma's unseeded
# repair-gen ladder that shares the base cell's group.
for M in "olmo3_7b|Olmo-3-7B-Instruct|Olmo-3-7B" "gemma2_9b|gemma-2-9b-it|Gemma-2-9B"; do
    IFS='|' read -r dir model short <<< "$M"
    G=eval_results/transfer/$dir/pgg
    BASE=$G/single_round; [ -d $BASE ] || BASE=eval_results/teacher_signal/$dir/pgg/deon_gate
    for A in SDPO GRPO; do
        $PY $MF seed-grid --group $G/single_round_sdpo_seed1 --group $G/single_round_grpo_v2_seed1 \
            --group $BASE --group eval_results/teacher_signal/$dir/pgg/deon_gate \
            --step 200 --seed 1 --model $model --principle deontological --channel $A \
            --title "$short, $A, step 200" --analysis-dir $CMP \
            --name ${dir}_$(echo $A | tr A-Z a-z)_seed1_s200
    done
done
# Mixed-game run (ACL app:multigame): PD-only seed 1 and PD+SH+CH at step
# 200, each with the Qwen v3 screen as base / +ctx reference.
R=eval_results/teacher_signal/qwen3_8b/pgg/single_turn_v3
$PY $MF seed-grid --group $T/single_round_sdpo_seed1 --group $R --step 200 --seed 1 \
    --model Qwen3-8B --principle deontological --title "Qwen3-8B, SDPO on PD, step 200" \
    --name qwen3_8b_sdpo_pd_seed1_s200 --analysis-dir $CMP
$PY $MF seed-grid --group $T/single_round_sdpo_pdshch_seed1 --group $R --step 200 \
    --model Qwen3-8B --principle deontological --title "Qwen3-8B, SDPO on PD + SH + CH, step 200" \
    --name qwen3_8b_sdpo_pdshch_seed1_s200 --analysis-dir $CMP
O=eval_results/transfer/olmo3_7b/pgg
$PY $MF multiround-compare --name olmo3_7b_opening_s200 --analysis-dir $CMP \
    --cell "base=$O/multi_round_n200_tok4096/cells/public_goods__none_3474481" \
    --cell "SDPO=$O/multi_round_n200_tok4096/cells/public_goods__none_3474482" \
    --cell "+ctx=$O/multi_round_incontext_n200_tok4096/cells/public_goods__deontological_3474485" \
    --cell "SDPO fabOpen=$O/multi_round_fabopen_n200_tok4096/cells/public_goods__none_3474487" \
    --cell "+ctx fabOpen=$O/multi_round_fabopen_incontext_n200_tok4096/cells/public_goods__deontological_3474491"
# Gemma-2-9B live play (ACL app:gemma): SDPO and GRPO seed 1 at step 200
# against base and base + principle in context.
G=eval_results/transfer/gemma2_9b/pgg
for spec in "SDPO GRPO|gemma2_9b_sdpo_grpo_seed1_s200" "SDPO|gemma2_9b_sdpo_seed1_s200"; do
    IFS='|' read -r chans name <<< "$spec"
    $PY $MF seed-multi --group $G/multi_round_sdpo_seed1 --group $G/multi_round_grpo_v2_seed1 \
        --group $G/multi_round_incontext --step 200 --seed 1 --model gemma-2-9b-it \
        --principle deontological $(printf -- '--channel %s ' $chans) \
        --name $name --analysis-dir $CMP
done
# Mixed-game run in live play (ACL app:multigame): SDPO seed 1 at step 200
# trained on PD only vs on PD + Stag Hunt + Chicken, against base and base
# + principle in context. The game set keeps them apart (seed_figures.py).
# 200 episodes, both openings (2026-11-11).
for open in "" _fabopen; do
    $PY $MF seed-multi --group $T/multi_round${open}_n200 --group $T/multi_round${open}_incontext_n200 \
        --group $T/multi_round${open}_sdpo_pdshch_seed1_n200 \
        --step 200 --seed 1 --model Qwen3-8B --principle deontological --channel SDPO \
        --name qwen3_8b_sdpo_pd_vs_pdshch_seed1${open}_s200 --analysis-dir $CMP
done
cp $CMP/figures/pdf/multi_round_qwen3_8b_seeds{,_fabopen}_s200.pdf \
   $CMP/figures/pdf/multi_round_olmo3_7b_seeds{,_fabopen}_s200.pdf \
   $CMP/figures/pdf/multi_round_qwen3_8b_sdpo_pd_vs_pdshch_seed1{,_fabopen}_s200.pdf \
   $CMP/figures/pdf/multi_round_gemma2_9b_sdpo{,_grpo}_seed1_s200.pdf \
   $CMP/figures/pdf/multi_round_qwen3_8b_sdpo_randlabels_seed1_s200.pdf \
   $CMP/figures/pdf/multi_round_qwen3_8b_thinking_vs_nothinking_seeds_s200.pdf \
   $CMP/figures/pdf/transfer_final_{olmo3_7b,gemma2_9b}_{sdpo,grpo}_seed1_s200.pdf \
   $CMP/figures/pdf/transfer_final_qwen3_8b_sdpo_{pd,pdshch}_seed1_s200.pdf \
   $CMP/figures/pdf/multi_round_compare_olmo3_7b_opening_s200.pdf \
   ~/moralgym-report/figures/ && echo "mirrored ACL live-play figures"

# Hanabi-RL transfer check (external checkpoint of Ramesh et al., ICML 2026,
# staged as qwen3_4b_grpo_hanabi_o3ratings_selfplay_ext-ramesh2026): the
# same in-play protocol on Qwen3-4B-Instruct-2507, base vs the released
# Hanabi-RL weights, PD and PGG. No single-round reference cells for 4B, so
# no --reference; distinct macro prefixes keep the 4B numbers apart from the
# 8B MRPD/MRPGG ones in the report preamble.
HANABI=eval_results/transfer/qwen3_4b
$PY scripts/analysis/make_results.py $HANABI/classic/pd_multi_round_hanabi_rl --macro-prefix MRPDHanabi
$PY scripts/analysis/make_results.py $HANABI/pgg/multi_round_hanabi_rl --macro-prefix MRPGGHanabi
cp $HANABI/classic/pd_multi_round_hanabi_rl/analysis/generated/numbers_MRPDHanabi.tex \
   $HANABI/pgg/multi_round_hanabi_rl/analysis/generated/numbers_MRPGGHanabi.tex \
   ~/moralgym-report/generated/ && echo "mirrored Hanabi-RL number macros"

# SDPO-4B control rows (same base, PD-trained via the teacher channel; base
# cells are symlinked from the hanabi_rl groups, not re-run). Figures are
# RENAMED on mirror: the spec derives figure names from <subject>_<game>, so
# these groups' figures would otherwise overwrite the hanabi_rl ones.
$PY scripts/analysis/make_results.py eval_results/post_training/qwen3_4b/classic/multi_round --macro-prefix MRPDFourB
$PY scripts/analysis/make_results.py eval_results/transfer/qwen3_4b/pgg/multi_round --macro-prefix MRPGGFourB
cp eval_results/post_training/qwen3_4b/classic/multi_round/analysis/generated/numbers_MRPDFourB.tex \
   eval_results/transfer/qwen3_4b/pgg/multi_round/analysis/generated/numbers_MRPGGFourB.tex \
   ~/moralgym-report/generated/ && echo "mirrored SDPO-4B number macros"

# The report shows ONE combined in-play figure (fig:hanabi-transfer): base +
# Hanabi-RL + SDPO s80/s150 across PD and PGG. Cells live in three groups,
# so the per-group spec can't draw it; make_figures reads the cells directly
# and mirrors the PDF itself.
CELLS_HANABI_PD=$HANABI/classic/pd_multi_round_hanabi_rl/cells
CELLS_HANABI_PGG=$HANABI/pgg/multi_round_hanabi_rl/cells
CELLS_SDPO_PD=eval_results/post_training/qwen3_4b/classic/multi_round/cells
CELLS_SDPO_PGG=eval_results/transfer/qwen3_4b/pgg/multi_round/cells
$PY $MF multiround-compare --name qwen3_4b_hanabi \
    --title "Qwen3-4B-Instruct-2507" \
    --cell "base=$CELLS_HANABI_PD/prisoners_dilemma__none__prose__multi_round__base_3338250" \
    --cell "Hanabi-RL=$CELLS_HANABI_PD/prisoners_dilemma__none__prose__multi_round__qwen3_4b_grpo_hanabi_o3ratings_selfplay_ext-ramesh2026-global_step_final_3338250" \
    --cell "SDPO s80=$CELLS_SDPO_PD/prisoners_dilemma__none__prose__multi_round__qwen3_4b_sdpo_pd_deon-repair-gen_tft_150-global_step_80_3357453" \
    --cell "SDPO s150=$CELLS_SDPO_PD/prisoners_dilemma__none__prose__multi_round__qwen3_4b_sdpo_pd_deon-repair-gen_tft_150-global_step_150_3357453" \
    --cell "base=$CELLS_HANABI_PGG/public_goods__none__decision_full__multi_round__base_3338250" \
    --cell "Hanabi-RL=$CELLS_HANABI_PGG/public_goods__none__decision_full__multi_round__qwen3_4b_grpo_hanabi_o3ratings_selfplay_ext-ramesh2026-global_step_final_3338250" \
    --cell "SDPO s80=$CELLS_SDPO_PGG/public_goods__none__decision_full__multi_round__qwen3_4b_sdpo_pd_deon-repair-gen_tft_150-global_step_80_3357453" \
    --cell "SDPO s150=$CELLS_SDPO_PGG/public_goods__none__decision_full__multi_round__qwen3_4b_sdpo_pd_deon-repair-gen_tft_150-global_step_150_3357453"

echo "report regenerated; commit + push ~/moralgym-report to publish"
