#!/bin/bash
# One cell per task: task i -> CELLS[i] = "<representation> <moral_value>".
CONFIG="${CONFIG:-/users/bickery/MoralGymVerl/configs/eval/pgg_screen_qwen3_8b.yaml}"
EPISODES="${EPISODES:-64}"
OUT="${OUT:-/users/bickery/MoralGymVerl/eval_results/_debug}"
TAG="${TAG:-pgg_list_smoke}"
# DESC=on|off — the mechanism preamble (docs/pgg_design.md §9.6)
IFS=';' read -ra CELLS <<< "${CELLS:-list none;list deontological}"
cell="${CELLS[${SLURM_PROCID:-0}]}"
[ -z "$cell" ] && exit 0
read -r REP MV <<< "$cell"
exec python3 -m moralgym_verl.eval.behavioral --config "$CONFIG" --checkpoint base \
  --moral-value "$MV" --num-episodes "$EPISODES" --protocol single_round \
  --representation "$REP" --game-description "${DESC:-on}" --save-raw-responses \
  --output "$OUT/${TAG}_${REP}_${MV}.json"
