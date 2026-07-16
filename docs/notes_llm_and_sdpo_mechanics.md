# Notes: forward pass vs. generation, and how SDPO builds its training signal

Personal learning notes (2026-07-13), written while designing the Session-1
teacher-signal eval. Kept here because the probe (`eval/logprob_probe.py`) and the
SDPO loss only make sense with this mental model.

## 1. A forward pass is not generation

A transformer is a function: *token sequence in → for every position, a
probability distribution over the next token*. All positions in parallel,
one pass, deterministic. It chooses nothing.

```
input:  ["The", "opponent", "defected"]
output: after "The"                   → P(next): {opponent: .31, game: .12, ...}
        after "The opponent"          → P(next): {defected: .55, cooperated: .28, ...}
        after "The opponent defected" → P(next): {last: .40, ".": .22, ...}
```

**Generation is a loop wrapped around forward passes:**
forward pass → *sample* one token (the only place randomness enters) →
append → repeat. A 50-token answer ≈ 50 forward passes + 50 sampling steps.

**Scoring (teacher forcing) needs no loop:** when the text already exists,
feed `[prompt + text]` through ONE forward pass and, at each position, look
up the probability of the token that is actually there. Nothing is chosen,
nothing is produced. 1 forward pass, deterministic. (This is also how LLM
pre-training works — corpora are teacher-forced, never generated.)

## 2. How SDPO creates its learning signal (three model calls)

Step 0 — generation (once, student prompt): the model plays; its whole
assistant turn (reasoning + "Answer: actionX") is the **trace**.

Pass 1 — score trace under the plain prompt (student; carries the gradient):

    <start_of_turn>user
    [plain game prompt]<end_of_turn>
    <start_of_turn>model
    [trace]

Pass 2 — score the SAME trace under the moral prompt (teacher; no_grad,
EMA weights):

    <start_of_turn>user
    [game prompt + "Moral value to follow: ..."]<end_of_turn>
    <start_of_turn>model
    [trace]

The trace tokens are INPUTS in both passes. Only the user turn differs, so
every per-token probability difference is attributable purely to the moral
context. The loss (JSD, alpha=0.5, `full_logit_distillation`) pushes the
pass-1 distributions toward the pass-2 distributions.

Code: `SDPO/verl/trainer/ppo/ray_trainer.py:762` (teacher batch),
`dp_actor.py:833` → `core_algos.compute_self_distillation_loss`.

## 3. "Advantage" analogy — useful but not literal

`loss_mode: sdpo` uses NO reward and no advantage function. But the
per-token quantity

    delta_i = log P_teacher(token_i) - log P_student(token_i)

plays the role an advantage plays in GRPO: per token, "make this more
likely" (delta>0) or "less likely" (delta<0). GRPO derives that signal from
rewards at episode/step level; SDPO derives it from the context gap at
every token. Reward remains computed for monitoring + optional {solution}
selection only.

## 4. Why the probe has two levels (eval/logprob_probe.py)

- **Probe A** (non-reasoning closer): answer follows the prompt at a fixed
  position → exact, noise-free comparison of the action distribution under
  both prompts (log-odds delta + two-way JSD). Clean but out-of-regime.
- **Probe B** (reasoning): the answer's probability depends on which trace
  was written, and traces can't be enumerated. Solution = the same trick
  SDPO uses: sample traces from the student prompt, hold each fixed, score
  under both prompts. Per-trace the comparison is exact; sampling noise
  only across traces (mean±std over num_traces).
  - `token_delta`: mean per-token delta over the trace = the distillation
    pressure SDPO's gradient will exert, measured pre-training.
  - `answer_delta`: log-odds shift at the answer given the SAME reasoning.

Specificity readout (both probes, per prior state CC/CD/DC/DD):
reciprocity = state-dependent sign flip (toward C in opp-C states, not
toward C in opp-D states). Uniformly positive delta = unconditional
cooperator — the failure mode Session 1 exists to catch.

Behavioral eval (teacher generates & plays) vs probe (teacher scores):
"is the teacher's behavior worth distilling?" vs "will the signal flow
through the loss?". If behavior shifts but deltas are ~0 → score-based
distillation won't transfer it → would need teacher-rollout distillation.
