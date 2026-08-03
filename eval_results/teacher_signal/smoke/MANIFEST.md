# MANIFEST — smoke (development artifacts, NEVER reference data)

Small-n pipeline checks. Nothing here is a result; never cite or compare.

- **prisoners_dilemma__deontological_2927718** (2026-07-29, e5eeab2): container
  end-to-end smoke after the pre-MVP soundness batch — 4-ep stage1a + probe A +
  probe B via launcher. Its probe-B bitwise-reproduces the original (pre-rerun)
  mvp/2927896 probe-B, confirming old-code determinism.
- **decay_smoke** (2026-07-29): 1-ep multi-turn decay probe; reproduces decay
  shape (answer_delta +3.25 r1 → ≈0 r2-5).
- **token_jsd_smoke** (2026-07-30, 0bdb0f8): first token_jsd outputs, n=1.
  Its DC/DD 2x-JSD hint did NOT hold at n=8/32 — uniform ~0.04–0.056.
