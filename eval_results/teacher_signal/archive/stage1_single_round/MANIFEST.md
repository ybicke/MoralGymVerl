# MANIFEST — stage1_single_round (ARCHIVED, SUPERSEDED)

**Status: superseded.** Pre-BOS-fix (double-BOS tokenization, fixed in dfb8147
2026-07-28). Absolute numbers retired; do not compare with any post-fix run.
Regenerable from git (pre-dfb8147 code reproduces bitwise within its version).

- **Date / jobs:** 2026-07-14. PD 2759076 (none) / 2759077 (deontological) /
  2759078 (utilitarian); Chicken 2759099/2759100/2759101 (none/deon/util);
  Stag Hunt 2759102/2759103/2759104 (none/deon/util).
- **Protocol:** stage1a — single fabricated-history round, `--opponent random`,
  200 eps/cell, T=1.0, fixed presentation, reasoning 512 tok.
- **Known defect:** run under the old lenient parser (up to 12% mis-assigned
  decisions). `behavioral.json` metrics are STALE; corrected tables were rebuilt
  offline from saved traces via `scripts/analysis/rebuild_state_table.py`.
  Corrected tables recorded in docs/teacher_signal_eval.md experiment index.
- **Superseded by:** MVP group (2026-07-29, PD none/deon, post-BOS-fix) as
  reference; Tier-1 sweep re-measures all cross-game cells at canonical T=0.7.
- **Qualitative findings that stand:** deontological→PD/Chicken reciprocity,
  utilitarian→Stag Hunt coordination / PD unconditional-cooperator failure.
