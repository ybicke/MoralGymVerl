# Post-training comparison: SDPO vs GRPO, qwen vs gemma

*(2026-08-24. Written from the generated tables in
`eval_results/post_training/<experiment>/analysis/results_*.md`; the
per-experiment readings are in the matching `analysis_*.md`. Companion to
`single_turn_screen_comparison.md`, which is the pre-training view of the
same wording.)*

Three trained arms, one wording (`deontological+repair+generosity`), one
game (PD, single fabricated-history round, surface-randomized prose), two
delivery channels:

| Experiment | Model | Channel | Training run |
|---|---|---|---|
| `qwen3-8b-pd-sdpo-deon-repair-gen` | Qwen3-8B | SDPO: wording in the teacher's context, no reward in the loss | qwen_run2_200 |
| `qwen3-8b-pd-grpo-deon-tft` | Qwen3-8B | GRPO: deon intrinsic reward (λ 0.75) through advantages, TFT opponent, wording never shown | grpo_deon_tft_200 |
| `gemma-2-9b-pd-sdpo-deon-repair-gen` | gemma-2-9b-it | SDPO, as above | gemma_run2_200 |

All post-training rows: checkpoint eval under the screen protocol, no
moral text in the prompt, fixed prose presentation, T = 0.7, 100
decisions per state (s.e. ≤ 5 points; differences under ≈ 14 are not
distinguishable).

## 1. State profiles

P(C | previous state), %; Δ_opp = P(C | C_O) − P(C | D_O).

| Policy | CC | CD | DC | DD | pooled | Δ_opp |
|---|---|---|---|---|---|---|
| qwen base, no context | 12 | 0 | 2 | 4 | 4 | +5 |
| qwen base + wording in context | 99 | 33 | 94 | 40 | 66 | +60 |
| **qwen SDPO s120** | 100 | 26 | 90 | 36 | 63 | +64 |
| qwen SDPO s200 | 100 | 41 | 98 | 49 | 72 | +54 |
| **qwen GRPO s180** | 96 | 1 | 72 | 3 | 43 | +82 |
| gemma base, no context | 5 | 1 | 5 | 8 | 5 | +1 |
| gemma base + wording in context | 100 | 43 | 83 | 6 | 58 | +67 |
| **gemma SDPO s120** | 99 | 53 | 96 | 7 | 64 | +68 |

**SDPO installs the teacher.** On both models the step-120 checkpoint
without any text reproduces the base model with the wording in context,
state by state, within noise — including the model-specific parts of the
teacher's profile: qwen's partial spiral escape (DD 36 vs 40) and gemma's
lock-in (7 vs 6). Distillation transfers the *behaviour under the
wording*, not the wording's intent: where the clause did not bind in
context (gemma, DD) it is not learned, and the training trajectory shows
gemma's DD falling (20 → 11) rather than rising.

**GRPO installs the reward.** The reward-optimal policy under TFT + deon
is C at CC/DC and D at CD/DD; GRPO converges to exactly that (96 / 1 / 72 / 3)
by step 120 and does not move afterwards. It is the sharpest reciprocator
in the table (Δ_opp +82) and has no forgiveness or escape — not because
it could not learn them but because the reward penalises them.

**Where the channels agree and disagree.** Same outcome at CC and DC
(both reward and wording say cooperate); opposite at CD and DD, where the
wording's "be first to restore" clause pulls SDPO toward cooperation
(qwen: 41 / 49 at s200) and the reward pulls GRPO to defection. The
CD/DD column is the channel difference in one line.

**Overshoot.** SDPO keeps drifting past its teacher after ~step 120
(qwen CD 26 → 41, DD 36 → 49; gemma CD 53 → 63): the EMA teacher is the
improving student plus the wording, so the push is re-applied every step
with no set-point. GRPO plateaus at its optimum. For SDPO the checkpoint
is a choice on a conditionality–generosity frontier (qwen s90 = maximum
conditionality, s120 = balanced, s200 = generous); for GRPO there is one
answer.

## 2. Reasoning traces

Normative-language rate = share of traces using any of the reviewed
moral terms; principle overlap = share reproducing ≥ 6 consecutive words
of the wording. Training-rollout traces at the evaluated steps; the base
rate of the vocabulary is ~2–6% (payoff talk of "exploiting").

| Arm | step 60 | step 120 | step 180/200 | principle overlap at end |
|---|---|---|---|---|
| qwen SDPO | 96–100% | 100% | 100% | 91–100% |
| gemma SDPO | 86–97% | 100% | 100% | 0–1% |
| qwen GRPO | 1–6% | 0–10% | 0–4% | 0% |

**The vocabulary arrives before the behaviour.** Under SDPO essentially
every trace reasons in moral terms by step 60, when cooperation at the
repair state is still 31% (qwen checkpoint) — the justification is
learned first, the decision follows. Under GRPO the vocabulary never
appears: the repair-state cooperation is argued from payoff prediction,
and late in training those arguments are often internally inconsistent
(the trace computes the higher payoff for defecting, then cooperates "to
maximize points") — the reward shifted the action and the explanation
was fitted afterwards.

**Recitation is model-specific.** Qwen ends up quoting the wording
verbatim in ~95% of traces, frequently attributed to "the guideline
provided" although none was; gemma paraphrases and never quotes.
"Cites the principle" is therefore not a general internalization
signature, only a qwen one.

**CD is reasoned, not reflexive.** SDPO traces at the sucker state argue
both clauses ("be first to restore" vs "you need not keep extending it")
and split accordingly — the conditionality survives internalization.

## 3. What this licenses, and what it does not

- SDPO changes both what the model does and how it argues; GRPO changes
  what it does. For a setting in which the agent must *reason and
  communicate* about cooperation (a commons with dialogue), only the
  distillation arm carries transferable content. This is a hypothesis
  with a mechanism, not a measured transfer: everything here is
  two-player, one-shot, fixed-presentation PD.
- Qwen is the substrate for transfer work (the "be first to restore"
  clause binds and distils); gemma is the low-adoption comparison point
  the screen predicted.
- Next instruments, in order: the PGG screen on these checkpoints
  (k-slope = conditionality with three counterparts, no dialogue), then
  multi-turn play.

## Caveats

Single seed per arm; parse failures ≤ 0.7% (a `### Action:` heading
case, fixed in the parser after these cells ran); GRPO used lr 2.5e-5 and
a TFT opponent where SDPO used lr 1e-5 and a random opponent (the
reward needs a deterministic opponent; prompts are byte-identical), so
the arms differ in optimizer and opponent as well as channel — the
state-profile contrast at CD/DD is robust to that (it follows the
reward's sign), the speed of convergence is not comparable.
