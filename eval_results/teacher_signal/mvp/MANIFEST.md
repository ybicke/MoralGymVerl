# MANIFEST — mvp (post-refactor runs, 2026-08-03; formerly mvp_recheck)

2026-08-03, code 755e55b (eval soundness batch 551ebca + probe-B phase
separation). Faithful protocol replication of the canonical mvp/ group
(stage1a, 200 eps, T=1.0, seed 42; probe B n=32 T=0.7; decay companion
config defaults). Purpose: confirm the 2026-08-03 eval refactors did not
change results. NOT a new reference — mvp/ stays canonical.

Comparison vs the July canonical group — now only in $STORE/git history (script: session scratchpad compare_mvp_recheck.py):
the new code consumes the SAME RNG draw sequence — early episodes are
bit-identical to canonical, and the first divergence is a single near-tie
token flip mid-generation ("chose"→"played", none ep 1; the length-equal
flip even re-syncs, eps 2-6 bit-identical again). After the first
length-changing flip the stream desynchronizes, so downstream episodes
are effectively resampled (~194/200 raw generations differ) →
statistical comparison, not bitwise.

CORRECTION (2026-08-03, job 2993459 = mvp_check/): the divergence
trigger is NOT the code version. An exact same-code same-seed
resubmission of 2991881 (node nid006136 vs nid007490) also diverged the
same way: eps 0-1 bit-identical, near-tie flip in ep 2, 2/200 lines
equal, (D,C) 46% vs 40%. Last-bit logit variation exists across
jobs/nodes generally; bitwise replay of SAMPLED rollouts is never
guaranteed. Deterministic layers (probe A, teacher-forced scoring of a
given trace) do replay byte-exactly. Consequences: the refactor is fully
exonerated, and none/(D,C) is a high-variance cell — three estimates
(0.66 July / 0.40 / 0.46 same-code) pool to ~0.51 (n=150), July's 0.66
being a +2.2σ draw. Interpretive follow-ups (behavioral DC delta likely
significant vs pooled baseline) are flagged in mvp/README.md status note,
pending verification.

- **prisoners_dilemma__none_2991881** (vs 2927894): 6/0/40/54 vs 8/4/66/52.
  (D,C) −26pp (z=2.6, p=0.009 uncorrected, not Bonferroni-x8-significant)
  — sole flag of the recheck, UNRESOLVED at n=50/state; other cells n.s.
  Prompts byte-identical 200/200.
- **prisoners_dilemma__deontological_2991884** (vs 2927896): 96/36/90/40
  vs 96/28/78/40, all n.s. Probe A EXACTLY bitwise identical to canonical.
  Launcher probe B here is n=8 — ignore, see the n=32 run below.
- **prisoners_dilemma__deontological_2991885** (vs 2927898, decay
  companion): all per-round stats ≤1.8 SEM; wrap-first signal ≈0 by round
  3 reproduced (token_jsd 0.040/0.017/0.005/0.003/0.002).
- **prisoners_dilemma__deontological_probeB_n32_T07** (job 2991886, vs
  2933208): all cells ≤1.4 SEM, 0 parse fails; DC remains a mode split
  (12/20 vs 17/15, n.s.) — "moderates, doesn't direct" holds. Staged to
  $STORE manually (direct sbatch --wrap skips launcher stage-out).

Verdict: refactor regression check PASSES on every probe and on 7/8
behavioral cells; none/(D,C) needs a 200/state tie-breaker before being
called anything but sampling noise.
