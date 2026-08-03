# MANIFEST — robustness (ARCHIVED, SUPERSEDED)

**Status: superseded.** Pre-BOS-fix (dfb8147). Absolute numbers retired;
qualitative conclusions below still stand and are cited in
docs/teacher_signal_eval.md. Regenerable from git.

- **Date / jobs:** 2026-07-16. 2761025 (none, R1 structure randomization),
  2761026 (none, R2 token randomization), 2775944 (deontological, R3 structure
  randomization, T=1.0 explicit — config default had already moved to 0.7).
- **Protocol:** stage1a single fabricated round, 200 eps, randomized
  presentation (per-cell flags in metadata), strict parser.
- **Findings that stand (directions, not absolutes):**
  - R1: base-model absolute C level is presentation-driven (matrix layout
    27→74%, first-mentioned label bias); own-prev-move signature persists;
    recip gap ≈0 in every slice for `none`.
  - R2: randomize_tokens gate passes (illegal 0.5%); DC/DD cooperation −18/−28pp
    → post-own-defection C partly rode on token identity.
  - R3: deontological reciprocity SURVIVES structure randomization (gap positive
    in every slice) → randomized-presentation SDPO training is a go.
- **Superseded by:** Tier-1 sweep ({matrix, matrix-rand, prose, prose-rand}
  axes at canonical T=0.7).
