# MANIFEST — mvp (CANONICAL group)

Post-BOS-fix reference results. Math/process/tables: README.md (kept separate
from docs/teacher_signal_eval.md for now — merge decision pending).
Rule: NEVER compare across temperature or code-fix boundaries.

- **prisoners_dilemma__none_2927894** (2026-07-29, e5eeab2): behavioral
  stage1a, 200 eps, **T=1.0**. P(C|CC,CD,DC,DD) = 8/4/66/52. Reference until
  Tier-1 re-measures this cell at canonical T=0.7 — do not mix with T=0.7 cells.
- **prisoners_dilemma__deontological_2927896** (2026-07-29, e5eeab2):
  behavioral stage1a, 200 eps, **T=1.0**. 96/28/78/40 → CC +88pp, CD +24pp vs
  none; DC/DD n.s. Same Tier-1 supersession caveat.
  **Probe-B files here are NOT the originals**: logprob_b.json/.traces.jsonl
  were overwritten 2026-07-30 by the token_jsd rerun (n=8, T=1.0, code
  0bdb0f8). Originals (e5eeab2) survive in the $STORE copy
  (/capstor/store/cscs/swissai/aa004/bickery/eval_results/teacher_signal/mvp/,
  staged 07-29) and are bitwise-regenerable from e5eeab2. n=8 answer_delta at
  DC/DD is resampling-unstable — superseded by the n=32 run below.
- **prisoners_dilemma__deontological_2927898** (2026-07-29, e5eeab2): 1-ep
  decay companion (ignore behavioral for tables). logprob_multiturn re-run
  2026-07-30 with token_jsd (0bdb0f8): decay re-certified — wrap-first teacher
  signal ≈0 by round 3 (token_jsd 0.039→0.002 by r4).
- **prisoners_dilemma__deontological_probeB_n32_T07** (job 2933208, 2026-07-30,
  0bdb0f8, direct srun — staged to $STORE manually): **DEFINITIVE probe-B
  table.** 32 traces/state, **T=0.7** (canonical), 0 parse fails. answer_delta:
  CC +4.54±0.40 (31/32 C-ward), CD +2.12±0.10 (32/32), DC +0.00±0.47 (17/15
  mode split — teacher moderates, doesn't direct), DD −1.29. token_jsd
  0.04–0.056 nats/token uniform. Retires the July/n=8 "DC +3.98" claim.
