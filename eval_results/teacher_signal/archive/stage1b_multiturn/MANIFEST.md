# MANIFEST — stage1b_multiturn (ARCHIVED, SUPERSEDED)

**Status: superseded.** Pre-BOS-fix (dfb8147). Absolute numbers retired;
qualitative conclusions stand. Regenerable from git.

- **Date / jobs:** 2026-07-16. Valid runs: 2761042 (none), 2761043
  (deontological), 2761044 (deontological+forgiveness). Dead dirs kept for the
  record: 2761027/2761028/2761029 (superseded already in July — do not use).
- **Protocol:** stage1b transcript mode, 5 rounds, opponents
  TFT/noisy_tft/AD/AC, wrap_position=first (training-exact), T at config
  default of the time.
- **Findings that stand:**
  - deon+forgiveness = best multi-turn cell: recovery after opponent defection
    vs TFT 58% by horizon (deon/none 6–7%), fewer spirals started.
  - Cost: sucker rate vs AD 45–48% (none 34%), re-cooperates ~60% at r3 —
    **origin of the CD/forgiveness farmability watch-item** (Session-2
    monitoring metric per 2026-07-31 decision; repair-clause work parked).
  - Plain deon suppresses spirals but does not exit them.
- **Superseded by:** Tier-1 includes 3 stage1b jobs at canonical protocol.
