#!/bin/bash
# One PGG eval cell per SLURM task: task i runs CELLS[i]. This is the
# PAYLOAD, not a submitter -- srun starts N copies and each picks its cell
# from $SLURM_PROCID, so N cells share one node's GPUs instead of running as
# N sequential jobs that each reload the model.
#
#   CELLS entries are "<representation> <moral_value> [game_description]",
#   semicolon-separated. game_description defaults to $DESC, itself off:
#   the mechanism preamble is the only wording that names which action is
#   pro-social, but it drives deontological to 100% at every k and erases
#   the k-slope (docs/pgg_design.md §9.6), so it is opt-in per cell.
#
#   EPISODES=64 TAG=mytag \
#   CELLS="decision_full none;decision_full utilitarian;decision_full deontological" \
#   srun --account=aa004 --partition=debug --time=01:30:00 --nodes=1 \
#        --ntasks-per-node=3 --gpus-per-task=1 -C thp_never\&nvidia_vboost_enabled \
#        --environment=moralgym_verl --export=ALL \
#        /users/bickery/MoralGymVerl/scripts/debug/pgg_smoke.sh
#
# Outputs land in $OUT as <TAG>_<representation>_<moral_value>.json plus the
# raw traces beside them -- read those, the rates alone have repeatedly been
# misleading (docs/pgg_design.md §9.5-9.7).
CONFIG="${CONFIG:-/users/bickery/MoralGymVerl/configs/eval/teacher_signal/qwen3_8b/pgg/_harness.yaml}"
EPISODES="${EPISODES:-64}"
OUT="${OUT:-/users/bickery/MoralGymVerl/eval_results/_debug}"
TAG="${TAG:-pgg_smoke}"
DESC="${DESC:-off}"
IFS=';' read -ra CELLS <<< "${CELLS:-list none;list deontological}"
cell="${CELLS[${SLURM_PROCID:-0}]}"
[ -z "$cell" ] && exit 0            # more tasks than cells: idle cleanly
read -r REP MV CELL_DESC <<< "$cell"
exec python3 -m moralgym_verl.eval.behavioral --config "$CONFIG" --checkpoint base \
  --moral-value "$MV" --num-episodes "$EPISODES" --protocol single_round \
  --representation "$REP" --game-description "${CELL_DESC:-$DESC}" \
  --save-raw-responses \
  --output "$OUT/${TAG}_${REP}_${MV}.json"
