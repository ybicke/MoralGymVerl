#!/bin/bash
# Pack N smoke cells on one debug node, one GPU per cell: task i -> cell i.
#   srun --partition=debug --nodes=1 --ntasks-per-node=3 --gpus-per-task=1 \
#        --environment=moralgym_verl scripts/debug/pgg_smoke_cells.sh
# Edit CELLS for the cells under test; CONFIG/EPISODES via env.
CONFIG="${CONFIG:-/users/bickery/MoralGymVerl/configs/eval/pgg_screen_qwen3_8b.yaml}"
EPISODES="${EPISODES:-64}"
OUT="${OUT:-/users/bickery/MoralGymVerl/eval_results/_debug}"
TAG="${TAG:-pgg_qwen_smoke}"
CELLS=("table off" "prose off" "prose on")   # "<representation> <game_description>"
cell="${CELLS[${SLURM_PROCID:-0}]}"
[ -z "$cell" ] && exit 0
read -r REP DESC <<< "$cell"
exec python3 -m moralgym_verl.eval.behavioral --config "$CONFIG" --checkpoint base \
  --moral-value none --num-episodes "$EPISODES" --protocol single_round \
  --representation "$REP" --game-description "$DESC" --save-raw-responses \
  --output "$OUT/${TAG}_${REP}_desc${DESC}.json"
