# Single-Turn Screen — Cross-Model Comparison

**Question:** Which moral principles, on which games, produce the
cooperative behavior and training signal we want to distill with SDPO —
and does the answer depend on the model?

Built alongside the per-model walkthroughs (2026-08-19). The per-model
analyses are authoritative; this doc holds only what is genuinely
comparative:

- Gemma: `../eval_results/teacher_signal/single_turn_screen_gemma-2-9b-it/analysis/analysis_gemma-2-9b-it.md`
- Qwen: `../eval_results/teacher_signal/single_turn_screen_qwen3-8b/analysis/analysis_qwen3-8b.md`

Layout: raw per-game notes and side notes first (kept for the detail),
condensed essential insights at the end.

## Raw cross-model notes (accumulating during the walkthrough)

- **PD:** utilitarian flips — artifact-failure on gemma (cooperation
  concentrated after own defection, Δ_opp ≈ 0) vs. maximal naive
  cooperator on qwen (97–99% in every state, conditioning erased).
  Generosity flips — paper-only on gemma (D_A D_O escape inside noise,
  prose floored at 6) vs. working balanced profile on qwen (D_A D_O in
  the 30–60 band in both representations, C_A D_O lowered by the
  revocation clause). Only deon-family reciprocity is stable across both
  models. Qwen is uniformly more steerable, and its baseline is a clean
  defection floor while gemma's matrix baseline carries the
  own-move-switch artifact.
- **Stag Hunt:** the baselines differ in kind — qwen is a genuine
  reciprocator (Δ_opp +70|+63, clean state profile), gemma an own-move
  switcher (Δ_opp +18|−3; 96|70 after its own defection) with a prose
  floor. Shared across models: the deon family deepens mutual-defection
  lock-in below each model's own baseline (D_A D_O gemma ≤22 vs 70|31,
  qwen ≤13 vs 32), and generosity takes the sharpest gap in both by
  trimming the exploited state, with D_A D_O floored in both — SH
  consistently blocks the escape clause. Divergent: utilitarian lifts
  gemma's SH to its welfare peak (70|56, inverted profile) but on qwen
  erases native structure at the ceiling; the balanced (PGG) winner is
  universalization on qwen and nobody on gemma — and universalization
  does least on gemma in exactly the game its wording names best.
- **Chicken:** baselines again differ in kind — qwen plays apt
  anti-coordination (Δ_opp −48|−32), gemma shows only the switch
  artifact (−7|−3 ≈ noise); the recall-hypothesis contrast at its
  clearest. The deon family inverts between models: on gemma, chicken is
  the one game where it keeps both level and conditioning (Δ_opp +25 to
  +48, D_A D_O 47–67), while on qwen it flattens to ceiling in matrix
  (Δ_opp −1 to +7, D_A D_O 89–98) with only prose retaining sensitivity
  (+18 to +28). Shared: virtue and universalization go near-ceiling
  unconditional in both models — the farmable profile. Utilitarian is
  maximally model-dependent: anti-cooperative on gemma (pooled 17|29,
  below the matrix baseline) vs. structure-preserving level-lift on qwen
  (72|80, Δ_opp kept negative).

## Side notes

- **Chicken: eval discriminator, not training ground (2026-08-19,
  game-specific — holds for both models).** Mapped onto the PGG rubric
  (sustain C_A C_O, resist C_A D_O, repair D_A C_O, escape D_A D_O),
  chicken's payoffs are anti-aligned on two of four states: yielding to
  a defector pays (S = 1 > P = 0), so the game endorses sucker
  persistence, and defecting on a cooperator pays (T = 4 > R = 2, with
  2R < T + S mutual cooperation is neither an equilibrium nor
  joint-best), so it endorses exploiting cooperators. It is aligned only
  on collapse escape (0/0 mutual defection — the one commons property PD
  and SH under-train). The screens show the conflict is real: principles
  respond with near-ceiling unconditional yielding (the farmable
  profile) while the probe-B teacher signal at D_A D_O points
  defect-ward — an internally conflicted distillation signal.
  Conclusion: keep chicken for evaluation, where it is the only game
  separating "cooperate more" from "play well" (exploitability and
  collapse-escape probe); train on PD/SH, and source collapse pressure
  from the PGG itself (low-multiplier/threshold variants) rather than
  from chicken. Matches the PGG design's PD parity anchor.

- **Model choice (hypothesis, 2026-08-19):** gemma-2-9b's recurring
  pattern — artifact-driven baseline, clause-level principles that do
  not bind in prompted behavior (repair, generosity's escape clause),
  probe-B teacher signal present at the target states while behavior
  does not move — reads as weak in-context adoption rather than a
  property of the principles. Qwen3-8B, same size class but a newer
  generation, binds the clauses and holds a clean baseline. If prompted
  adoption predicts teacher quality for SDPO, fine-tuning a newer/
  stronger base model may be the better substrate; gemma then serves as
  the lower-capability comparison point. Open check before acting on
  it: gemma's probe-B signal at the unbound states is directional and
  significant, so SDPO training might still transfer what prompting
  cannot elicit — the two hypotheses separate only after a training
  run.
- **Textbook-recall hypothesis (2026-08-19):** qwen's apt baselines in
  all three games (PD defection floor, SH reciprocity, chicken
  anti-coordination) may be pretraining recognition of canonical games
  rather than payoff reasoning — apt at the canonical rendering,
  drifting when it is scrambled (2 → 42%); gemma lacks the recall
  (artifact baseline). Corollary: the deon conditioning *sharpening*
  under the same scrambling is not recall-bound — the strongest reading
  of the robustness result. Implication: fixed-presentation baselines
  partly measure recall; randomize surfaces and add disguised
  (cover-story) framings. Testable via payoff-resampled and disguised
  variants.

## Essential insights (condensed)

1. **The baselines differ in kind, not degree.** Qwen plays every game
   aptly (PD defection floor 0–12%; SH reciprocity Δ_opp +70|+63;
   chicken anti-coordination −48|−32); gemma shows one heuristic
   everywhere (own-move switch in matrix, defection floor in prose).
   "Lift over baseline" is therefore not comparable across models —
   compare state profiles instead.
2. **Only deon-family reciprocity is model-stable.** Opponent-
   conditioning from deontological(+repair) appears in every game on
   both models. Everything else flips: utilitarian is artifact-failure
   (gemma PD), max naive cooperator (qwen PD/SH), welfare winner
   (gemma SH), structure-eraser (qwen SH), anti-cooperative (gemma
   chicken), structure-preserving (qwen chicken). Principle efficacy is
   a model property; only the deon backbone is portable.
3. **The balanced (PGG) profile is model- and game-dependent.** Qwen:
   generosity in PD (D_A D_O in the 30–60 band, C_A D_O lowered),
   universalization in SH. Gemma: nobody (chicken's nominal fit is the
   game meeting the principle halfway). The generosity rider is
   validated on qwen only.
4. **Robustness inverts between models.** Under full surface
   randomization gemma's deon gap erodes (+60/+55 → +51/+38,
   layout-driven) while qwen's sharpens (+34/+45 → +58/+62); both
   baselines drift toward noise. The conditioning is content-anchored
   (principle text + opponent's stated move), not surface- or
   recall-bound.
5. **Representation: prose on both models, for different reasons.**
   Gemma: clean floor baseline and coherent principle ordering (matrix
   carries the artifact). Qwen: levels match matrix but conditioning
   roughly doubles (deon+repair PD Δ_opp +61 vs +35), and chicken
   retains opponent-sensitivity only in prose. Caveat: prose is the
   layout-sensitive representation on gemma.
6. **Games have roles, not ranks.** PD is the instrument (floor
   baseline, full principle spread, both models). SH is the compliance
   null (pre-solved on qwen, immovable artifact on gemma; the deon
   family deepens D_A D_O lock-in below both baselines and SH blocks
   the escape clause in both). Chicken is an eval discriminator only.
7. **Teacher signal ≠ prompted behavior, in both directions.** Gemma:
   probe-B directional at states where behavior does not move
   (generosity D_A D_O prose +1.74 → +3.02) — training may elicit what
   prompting cannot. Qwen chicken: behavior at ceiling while the probe
   points defect-ward at D_A D_O. Never select principles on behavior
   alone.

**Implications (Session-2 / PGG):** deon+repair backbone, prose,
surface randomization — the only model-stable, robustness-positive
combination; generosity rider qwen-validated. Prefer qwen (or a newer
model) as substrate, keep gemma as the lower-capability comparator.
Train on PD/SH; chicken eval-only, collapse pressure from PGG variants.
Report state profiles and baseline-differenced Δ_opp under matched
presentation.

**Hypotheses & tests:** weak-ICL vs. trainable-anyway on gemma
(separates only via a training run); textbook recall (payoff-resampled
and disguised cover-story framings).
