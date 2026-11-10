# PGG single-turn screen v4 (deontological wordings): what fixes the label inversion

Companion to the generated `results_pgg_qwen3-8b.md` (Tables 1–2, figure).
Qwen3-8B base, thinking off, T = 0.7, seed 42; `decision_full` payoff
block, fixed presentation, `game_description` off, single_round over the
8 balanced states × 50 episodes; 3 arms (deon_fairplay / deon_explicit /
deontological+hint), job 3244650, 2026-09-01, 0 parse failures.
Baseline throughout: the v3 screen (`../single_turn_v3/`, job 3185343,
same protocol, code drift cosmetic): none 1 | 1, deontological 62 | 40
with 22% label inversion, utilitarian 96 | 95, universalization 69 | 64.

Hand-written. `results_pgg_qwen3-8b.md` beside it is regenerated
wholesale by `scripts/analysis/make_results.py`; re-check the quoted
numbers after any regeneration.

## 1. The question

The v3 deep dive (`../single_turn_v3/analysis/analysis_pgg_qwen3-8b.md`
§3) showed the deontological arm's weakness is not game comprehension
but the mapping of the principle's words onto the semantically empty
labels: "those who act in good faith" is resolved by MAJORITY (good
faith attached to keep in 49% of traces at k = 0, to contribute in 46%
at k = 3), and 22% of traces attach "exploit" to the contribute label
and keep. v4 tests the two candidate fixes in isolation:

- **referent fix** — reword the principle so the protected party is
  named by the game act: `deon_explicit` ("those who *contribute* in
  good faith", exploit stems kept so Table 2 stays comparable) and
  `deon_fairplay` (Hart 1955; Rawls 1964; Cullity 1995 fair play — "do
  not benefit from what others contribute without contributing"; no
  exploit stems, so its valence is silent by design);
- **mapping fix** — keep the screened wording verbatim and append the
  label-naming rider (`hint`: "action3 is the option that contributes
  to the group and action4 the option that keeps for yourself"),
  privileged context in the SDPO sense.

## 2. Headline: the hint fixes it, rewording does not

Mean P(C) as C_A | D_A; inversion pooled over k:

| arm | mean | k=0 | D_A curve | inversion C_A / D_A |
|---|---|---|---|---|
| deontological (v3) | 62 \| 40 | 28 \| 4 | 4 → 86 | 14% / 30% |
| deon_explicit | 66 \| 42 | 32 \| 6 | 6 → 84 | 11% / 31% |
| deon_fairplay | 62 \| 57 | 30 \| 32 | 32 → 90 | (3% / 4%, mostly silent) |
| **deontological+hint** | **88 \| 70** | 64 \| 32 | 32 → 98 | **6% / 14%** |

- **`deon_explicit` ≈ `deontological` within noise on every measure**
  (Wilson s.e. ≤ 7 pp per state, ≤ 3.5 pp pooled). The inverted traces
  show why the referent rewording cannot work: the model re-attaches
  "contribute" to whatever the majority did — *"If you now choose
  action3, you are exploiting the trust of the others, taking advantage
  of their consistent choice of **action4**"*; *"choosing action3
  exploits the other players' good faith **contribution**"* (their
  "contribution" being keeping). The conformity heuristic absorbs any
  conduct- or act-word the wording supplies; with empty labels the
  ambiguity is a property of n > 2, not of the phrasing.
- **`deontological+hint` halves inversion in both halves (30 → 14,
  14 → 6), raises the correct-orientation share (D_A 27 → 48%), and —
  decisively — keeps the k-slope**: 64 | 32 at k = 0 against 100 | 98 at
  k = 3. This is NOT the `game_description` failure (§9.6: 100% at every
  k, compliance). The rider names what the actions *do*, never which is
  right, so the principle still has to decide whom to protect — and
  still declines to bind when nobody contributed. Among valence-correct
  traces P(C) is 96–98%.
- **`deon_fairplay`** lands between (62 | 57): monotone in k in both
  halves and the smallest own-move anchoring of any deontological arm
  (+5 vs +18–22), but its Table 2 silence makes the label reasoning
  unauditable, and its k = 0 level (30 | 32) shows the "contribute"
  vocabulary pulls mildly unconditionally.

## 3. The residual at (D, 0) under the hint

The hint arm's largest departure from v3 is (D, 0): 4 → 32%. Trace
classes (n = 50): 22 inverted (19 keep — the conformity reading
survives the hint at the state where the majority kept: switching to
contribute is called the exploit), 7 correct-tagged (6 contribute — the
over-extended "keeping takes advantage" read, with nobody to take
advantage of), 16 silent (6 contribute on "action4 does not help the
group" welfare wording — halo from the rider's phrase "contributes to
the group"). So the k = 0 lift is roughly half halo, and the surviving
inversion is concentrated exactly here. Since at k = 0 the principle
genuinely binds nothing (no contributor to protect), the *decisions*
are defensible either way; the *rationales* of the inverted class are
still wrong and are what a filter should drop.

## 4. What this settles for training

**The comprehension bar differs by algorithm, and this screen only
gates SDPO.** SDPO distills the teacher's *tokens*: the rationale is
the training signal, so the teacher must read both the game AND the
principle correctly — an inverted rationale is a mislabeled training
example even when the action happens to be right. GRPO never shows the
model the principle text; the reward is computed from actions by the
environment (`r_deon = −λ·k_prev/(N−1)` needs no language at all), so
GRPO needs only the *game* to be understood — which `decision_full`
already delivers at base (v3: 0 self-count / frozen-others / own-payoff
errors, base floor 1 | 1). The v4 result is therefore a teacher-quality
result for the SDPO arm, not a prerequisite for the GRPO twin.

- **SDPO teacher = `deontological+hint` on `decision_full`.** Highest
  correct-rationale share with conditionality intact. Residual
  filtering is cheap insurance: drop valence-inverted rollouts
  (6–14%, concentrated at k = 0) at teacher-rollout time.
- **Training-side hint must be per-episode.** The static `hint` entry
  hard-codes action3/action4 and is refused under randomized labels
  (behavioral.py guard); training randomizes labels, so
  `reward_fn`'s principle mode formats the same sentence from the
  episode's sampled labels. Keep one template in `moral_values.py` so
  the screened wording and the training wording differ only in the
  substituted labels.
- **`deon_fairplay` is the reserve arm** if a hint-free teacher is ever
  required (e.g. to claim no privileged context at all): moderate
  conditional lift, least own-move anchoring, but unauditable valence.
- **`deon_explicit` is closed**: a negative result worth reporting
  (referent rewording cannot fix a label-mapping failure at n > 2),
  not a training candidate.

**Not addressed here:** the k = 0 halo under the hint (watch it in the
trained student, where the hint is absent at eval), presentation
robustness, regime nulls, multi-round.
