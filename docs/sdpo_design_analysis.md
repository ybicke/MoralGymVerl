# SDPO Experimental Design for MoralGym — Analysis & Decisions

*2026-07-10. Based on a full read of Hübotter et al., "Reinforcement Learning via
Self-Distillation" (arXiv:2601.20802v2), the official lasgroup/SDPO code
(`~/SDPO`, upstream HEAD `7c457fc`), and the MoralGymVerl port.*

## 1. How MoralGym differs from every setting in the paper

The paper evaluates three regimes: §3 scalar-reward-only (solutions from the
rollout group act as feedback), §4 rich environment feedback (code errors,
failed tests), §5 test-time training on single hard questions. MoralGym fits
none exactly:

| Dimension | Paper (all settings) | MoralGym |
|---|---|---|
| Correctness | Verifiable, binary | Researcher-defined, **graded** (normalized payoff + moral term) |
| Feedback source | Raw environment state (objective) | **Synthesized** critique from reward_fn (we control its content) |
| Response length | 8k tokens, long CoT | 32 tokens (one action), 512 with reasoning |
| Where dense credit helps | Localizing the bug in a long trace | Group-tie breaking; later, per-round credit in multi-turn |
| Base model | Qwen3-8B (strong ICL) | Gemma-2-9B-it (mid-tier ICL) |
| Goal | Task accuracy | Generalizing **disposition** (cooperation) + reciprocity structure |
| Training | Full FT | LoRA rank 32 |

Two consequences dominate the design:

**(a) The graded-reward / state-coverage problem.** The official code defines a
demonstration by an *absolute* threshold (`reward >= success_reward_threshold`).
Our reward maxima differ by state: in opp-cooperated states the best outcome is
CC = 0.75; in opp-defected states (TFT retaliating) the best is DD = 0.25 or
CD = 0.0. A single threshold that admits CC (e.g. 0.7) means **opp-defected
states can never produce a demonstration** — retaliation/forgiveness behavior
gets zero SDPO signal unless environment feedback is enabled. GRPO's
group-relative advantage handles this automatically; absolute-threshold SDPO
does not. Options:

1. Enable `include_environment_feedback: true` → all states get teacher signal
   (the paper's env-output channel exists precisely for never-solved questions).
2. Patch a group-relative success criterion (e.g. `reward >= group_max - eps`)
   into `_collect_solutions_by_uid` — small, isolated change (~5 lines in
   `ray_trainer.py`), but a fork-divergence from upstream.
3. Accept partial coverage during the parity phase (Phase 1 status quo).

**(b) The feedback-semantics decision (research-claim decision, not a tuning
knob).** Our `reward_fn.py` critique is *prescriptive*: it states counterfactual
payoffs AND the moral rationale ("cooperation would have been better",
"betrayal violates reciprocity"). Conditioning the self-teacher on this makes
SDPO ≈ **on-policy context distillation of a morally-instructed model into the
unprompted policy** (Constitutional-AI-flavored). That may be exactly what we
want for disposition-shaping — but it changes the scientific claim from "the
model discovers cooperation from game/moral reward" to "the model internalizes
verbalized moral instruction." A *descriptive* variant (actions + payoffs +
joint outcome, no prescription) preserves the discovery claim while still
breaking the scalar bottleneck. These are different experiments; pick per
research question, or ablate (it's a one-function change in `_build_feedback`).

## 2. Axis-by-axis choices

| Axis | Paper's finding | Choice for MoralGym | Rationale |
|---|---|---|---|
| Feedback composition | output + group solution best (Table 6); incl. own attempt hurts entropy | Phase 1: solutions only (parity). Phase 2: + feedback. Never include own attempt | Table 6 transfers; entropy matters doubly for us (reciprocity requires state-conditional behavior, not unconditional C) |
| Success criterion | binary pass | **0.7** now (fixes unreachable-0.8 bug); revisit group-relative later | Achievable scores: 0.75 CC, 0.25 DD, 0.0 CD, −2 exploit, −6 parse-fail |
| Granularity | logit > token > sequence | logit-level; full-vocab OK at 32 tokens, **restore top-K (100) before reasoning/multi-turn** | Gemma vocab 256k × long responses = memory blowup |
| Divergence | JSD (§3), reverse-KL (§4) | JSD (α=0.5) | Matches §3 (our regime); symmetric term is gentler on entropy |
| Teacher reg. | trust-region ≳ EMA ≫ none | EMA, rate 0.01–0.05 | Already wired; LoRA-EMA verified structurally sound (teacher = base + EMA'd adapter) |
| Pure vs hybrid λ | hybrid (λ=0.9) rescues weak models | Start pure (parity); **expect to need hybrid** | Gemma-2-9B mid-tier ICL + non-verifiable domain → SDPO advantages only as good as Gemma's moral retrospection; GRPO term anchors to actual reward. Hybrid is NOT in released code — needs a small `dp_actor.py` patch (add λ·pg_loss) |
| Self-reprompt on success | paper template: yes; code default: no | keep `dont_reprompt_on_self_success: true` | Coop-biased groups usually have ≥2 successes, so lone-success masking is rare; avoids entropy collapse |
| On/off-policy | SDPO strictly on-policy | keep 2 mini-steps (NeMo-RL parity); `is_clip: 2.0` covers it | Paper flags off-policy SDPO as future work; small deviation |
| Rollout IS correction | token-level, clip 2.0 | re-enable after parity phase | LoRA-refit + FlashInfer mismatch is the case it exists for |

## 3. Proposed phases

**Phase 0 — teacher diagnostic (no training, ~1 debug job).** Replicate the
Table 6 "teacher before training" measurement on MoralGym: reprompt Gemma-2-9B
with each feedback variant (none / solution / descriptive critique /
prescriptive critique) across the 4 history states, and measure (i) teacher
coop/reciprocity-rate shift vs student, (ii) per-token log-ratio
`log q(y)/π(y)` localization on the action token. This directly tests the
paper's core assumption (teacher > student given feedback) for OUR model and
task before spending any training compute. If the teacher doesn't shift toward
reciprocity here, SDPO cannot work downstream and we stop.

**Phase 1 — parity (as planned, unblocked).** Fix threshold 0.8 → 0.7 (and the
wrong "deon bonus" comment). Solutions-only, full-logit, JSD, EMA 0.01, pure
SDPO vs the GRPO reference run. Health metrics:
`self_distillation/success_sample_fraction > 0`, `actor/grad_norm > 0`,
reciprocity_rate on eval.

**Phase 2 — feedback ablation.** `include_environment_feedback: true`;
descriptive vs prescriptive critique arms. This is also the fix for the
state-coverage problem (opp-defected states get signal).

**Phase 3 — hardening & extensions.** λ-hybrid if Phase 1/2 underperform GRPO;
top-K 100; rollout IS correction; then multi-turn — where SDPO is most
promising for us: feedback = full game trace summary, logit-level advantages
give per-round credit without the per-step-advantage/RTG machinery that hasn't
fully worked in NeMo-RL.

## 4. Known blockers / bugs (as of 2026-07-10)

1. **`success_reward_threshold: 0.8` unreachable** (max score 0.75 — deon is
   penalty-only, config comment wrong). SDPO loss masks to zero everywhere;
   run trains nothing, silently. Fix to 0.7. — `configs/verl/sdpo_pd_tft.yaml:49`
2. `norm_adv_by_std_in_grpo: true` is inert in SDPO mode (advantages unused);
   no hybrid exists in released code.
3. `ref.fsdp_config.param_offload: true` is inert in the colocated worker
   (offload flags read from actor config only); teacher stays on GPU (fine).
