# Multi-turn: execution, update schemes, and SDPO

*(Discussion notes, 2026-08-06 — how multi-turn rollouts work in verl/NeMo-RL, the
per-turn splitting scheme, and what it means for GRPO credit assignment and SDPO.)*

## The rollout model, confirmed

In verl (and NeMo-RL), one multi-turn rollout **is one growing context window**:

```
┌────────────────────── one rollout = one conversation ──────────────────────┐
│ user:      [game rules + "choose"]                ← initial prompt P       │
│ assistant: [action a1]                                                     │
│ user:      [“they played D, you got 0 pts. choose”]  ← env message         │
│ assistant: [action a2]                                                     │
│ user:      [“they played C, you got 4 pts. choose”]                        │
│ assistant: [action a3]                                                     │
│ ...                                                                        │
└────────────────────────────────────────────────────────────────────────────┘
```

The history is *literally sitting in the context* — nothing needs to be restated. So in
transcript mode, the per-turn env message only needs the **new** information: opponent's
move, payoffs, "choose again."

This exposes a design wart in the current setup worth knowing: `game_interaction.py`
builds each next user message with the full `build_prompt(...)` — complete game rules
**plus** the Markov-1 "last round you played X" summary — every round (~350 tokens each;
the yaml comment in `configs/verl/sdpo_pd_tft.yaml` sizes it). That prompt was designed
for the stateless mode and is doing double duty. In transcript mode it's redundant three
ways: the rules are already in context, the last round's moves are already in context,
and the summary re-tells what the model just did. Standard agentic practice is a lean env
message. (Whether repeating the rules *helps* weaker models attend to them is an
empirical question — but it should be a deliberate choice, not an accident of code
reuse.)

On model type: at 5 rounds, raw context length is a non-issue for any model. The thing
that *does* grow is reasoning traces — a CoT model emits hundreds of tokens per turn, and
the common fix (built into most chat templates and verl's multi-turn path) is to keep
only the final answers of *previous* turns in context, dropping their thinking blocks.

### The restatement flaw, sharpened (2026-08-06 follow-up analysis)

Verified in code: eval transcript mode has the identical restatement — `run_episode`
(trajectory.py) calls the full `build_prompt(...)` every round and the chat policy
(generation.py `make_chat_policy_fn`) appends it verbatim, mirroring
`game_interaction.py`. Parity is intact; it is a shared design wart, not an
eval/training mismatch. Three consequences beyond wasted tokens:

1. **Markov-1 shortcut.** Every latest env message is a complete, self-sufficient game
   description plus a memory-1 state summary — the model never *needs* the transcript.
   Current transcript mode may therefore be behaviorally equivalent to the stateless
   protocol, making history-conditioning (noisy-TFT vs grim, exploitation trends,
   grudge/forgiveness lags) optional instead of necessary. Lean env messages invert
   this: rules once in message 1, per-turn messages carry only new information — then
   attending to history is the only way to know the game state.
2. **Wrap-first decay aggravation.** The moral value is wrapped into message 1, but
   every later env message restates the full task *without* the value in the most
   recent (most attended) position. Part of the measured decay-to-round-2 may be caused
   by the restatement displacing the wrapped message, not by distance alone.
3. **Keep the instruction anchors.** The closing question ("What action would you take
   in order to achieve the highest possible score in points?") and the answer-format
   instruction ("choose either action3 or action4. Your answer:") stay per-turn — the
   first carries the objective, the second the output contract, and dropping either
   trades a ~15-token restatement for instruction decay / parse-failure inflation on
   small models. That repetition is deliberate; the rules/summary repetition is
   accidental code reuse. Only the payoff block is optional, behind
   `restate_rules_per_round`.

**Cheap empirical check before any code change:** if the shortcut hypothesis holds,
existing transcript cells should behave near-identically to matched stateless
(`transcript=False`) cells — compare per-state conditioning tables of paired Stage-1b
runs offline (no GPU needed).

**Fix shape:** `build_env_message(...)` (opponent move, payoffs, round counter if
`show_horizon`, question, answer format) for rounds ≥2; round 1 keeps the full
`build_prompt`. Must land in BOTH `run_episode`/eval and `game_interaction.py` in
lockstep, plus a lean parse-failure reprompt. Comparability caveat: post-change results
are not comparable to existing stage1b_transcript cells — re-run baselines.

## The three update schemes, schematically

**Scheme A — trajectory-level (what verl gives you today):**

```
1 episode → 1 training sample
tokens:   [P][a1][u2][a2][u3][a3][u4][a4][u5][a5]
loss mask:    ██      ██      ██      ██      ██     (env turns masked out)
reward:   R = r1+r2+r3+r4+r5, one scalar at the end
advantage: ONE number, shared by all five actions
```

The diagnosis: GRPO must stumble on *whole good trajectories* by chance, and a good
round-3 decision inside a bad episode gets punished. That is the credit-assignment
problem.

**Scheme B — per-turn splitting (the state-of-the-art fix):**

The crucial point: **the rollout is unchanged.** The agent still plays one conversation
with full history. Splitting happens afterwards, at data-preparation time:

```
1 episode → 5 training samples

s1: prompt = [P]                          response = [a1]   reward = G1
s2: prompt = [P a1 u2]                    response = [a2]   reward = G2
s3: prompt = [P a1 u2 a2 u3]              response = [a3]   reward = G3
s4: prompt = [P ... u4]                   response = [a4]   reward = G4
s5: prompt = [P ... u5]                   response = [a5]   reward = G5

where Gt = rt + rt+1 + ... + r5   ← reward-TO-GO, not the round reward
```

Two details make or break this:

1. **The reward must be reward-to-go, not the immediate round payoff.** This is critical
   in *this* game specifically: with immediate reward only, defection strictly dominates
   every round of PD and the agent must learn to defect. Cooperation is only rational
   through future reciprocity — which is exactly what `Gt` (this round plus everything
   after) encodes.
2. **Advantages are computed within groups of comparable samples**, keeping the
   critic-free GRPO recipe: either group all turn-t samples across the G rollouts of the
   same initial prompt (the MT-GRPO family), or — the GiGPO refinement, tailor-made for
   this game — group samples by their *game state*, i.e. every decision taken from
   (agent_prev=C, opp_prev=D) across the whole batch forms one group, regardless of
   episode or turn index.

## Why this is NOT the same as single-step training

| | single-step (stage-1a style) | per-turn split |
|---|---|---|
| gradient granularity | one decision | one decision — same |
| the state in the prompt | **fabricated**, sampled by you | **real history the policy actually produced** |
| what history can express | memory-1 only | full transcript: trends, grudges, noise vs. grim |
| reward | one round's payoff | reward-to-go (future consequences included) |

So per-turn splitting gives you single-step's clean credit assignment *while* training a
genuinely history-conditioned policy on states drawn from real play. It's the best of
both, which is why the literature converged on it.

One clarification on the "intermediate update to guide decisions" idea: nobody updates
weights *during* an episode. The episode is rolled out fully (frozen policy), then split,
then a batch of split samples produces one gradient update. "Per-turn" refers to sample
granularity, not update timing.

## And SDPO?

Per-turn splitting is precisely what makes multi-turn SDPO coherent again. Each split
sample has its own prompt — so each one can be wrapped with the teacher context
individually:

```
student prompt:  [P a1 u2 a2 u3]                    → distribution over a3
teacher prompt:  [wrap(P, moral_value) a1 u2 a2 u3] → distribution over a3
                  └── value re-attached per sample; distill on a3's tokens
```

The teacher signal no longer has to survive five rounds of transcript burial — which is
exactly the wrap-first decay the signal-decay probe measured dying after round 2.
Multi-turn SDPO becomes "N single-turn SDPO updates on real histories," and the question
"does multi-turn SDPO even make sense?" gets a concrete answer: not in wrap-first form;
yes in split form.

**Practical order:** (1) lean env messages in transcript mode — small prompt change;
(2) per-turn splitting with reward-to-go and turn-index groups — moderate data-pipeline
work in verl; (3) GiGPO-style state grouping as the upgrade — in a 2×2 game the four
states repeat so densely that the step-groups are enormous, which is the best-case regime
for that method.

---

# Part 2 — Scheme B in detail (worked examples)

## 2.1 Rollout, split, update: three separate phases

Nothing about the rollout changes: no re-prompting, no replaying, no splitting "during"
play. Splitting is bookkeeping on the recorded transcript.

- **Phase 1 — rollout (play).** The model plays one ordinary multi-turn conversation
  against the opponent, exactly as today. The whole conversation is logged.
- **Phase 2 — split (data prep).** The transcript already *contains* every intermediate
  state, because the state at turn t is simply "the conversation up to turn t". Cut the
  one recording into 5 samples by taking prefixes:

  ```
  recorded transcript:  [P][a1][u2][a2][u3][a3][u4][a4][u5][a5]

  sample 3 = (prompt: [P a1 u2 a2 u3],  response: [a3])
                       └─ first part of the recording — copied, not regenerated
  ```

  Chess analogy: record a game, then afterwards make one training example per move —
  "position before the move → move played → how the game ended from here". The record
  contains them all; no replay needed.
- **Phase 3 — update.** One gradient step over the whole batch of split samples (all
  turns of all episodes together, see 2.4). Weights are never updated between turns.

## 2.2 Reward-to-go: forward-looking by construction

Each sample's reward is the **reward-to-go**

```
Gt = rt + rt+1 + ... + r5        (from turn t ONWARD — the future, not the past)
```

Direction matters: G2 = r2+r3+r4+r5, **not** r1+r2. A decision can only influence what
comes *after* it — r1 was already banked when a2 was chosen, so crediting a2 with r1
would reward it for something it could not affect. Each sample splits the episode at its
turn:

```
sample for turn t:   [ past ────────────][ decision ][──────────── future ]
                      goes in the PROMPT     at         goes in the REWARD Gt
                      (the state St)                    (the consequences)
```

**Past → prompt, future → reward.** The past is not lost — it is exactly what the model
conditions on; the future is what the decision is judged by.

Numeric example (PD payoffs T=4, R=3, P=1, S=0), defect-first episode vs tit-for-tat:

```
round:        1     2     3     4     5
agent:        D     C     C     C     C
opponent:     C     D     C     C     C      (TFT mirrors the agent's previous move)
round reward: 4     0     3     3     3      (T, S, R, R, R)

G1 = 4+0+3+3+3 = 13
G2 =   0+3+3+3 =  9
G3 =     3+3+3 =  9
G4 =       3+3 =  6
G5 =         3 =  3
```

All-cooperate episode: every round pays R=3, so G1 = 15. The point in one line: the
**immediate** reward says round-1 defection was the best possible move (4 > 3); the
**reward-to-go** says it was a mistake (13 < 15), because it *contains the retaliation
it caused*. Reward-to-go holds a single decision accountable for its future without
smearing credit over the whole episode.

## 2.3 Advantages: turn-aligned groups (3-rollout example)

GRPO never uses rewards raw — each sample is compared against a group of comparable
samples, and the advantage is the deviation from the group. With per-turn splitting the
groups are **turn-aligned**: all turn-t samples across the G rollouts of the same game.
Why turn-aligned? A G3 (3 rounds remaining, max 9) and a G1 (5 remaining, max 15) live
on different scales — comparing them raw would say "turn-1 decisions are always better".

Three rollouts vs TFT (T=4, R=3, P=1, S=0):

```
A: C C C C C   opp: C C C C C   r = 3,3,3,3,3   G = 15, 12,  9,  6,  3
B: C C D C C   opp: C C C D C   r = 3,3,4,0,3   G = 13, 10,  7,  3,  3
C: D C C C C   opp: C D C C C   r = 4,0,3,3,3   G = 13,  9,  9,  6,  3
        ↑ B defects mid-game        ↑ C defects first, then recovers

Advantages A_s = Gt − turn-group mean:

              t=1      t=2      t=3      t=4      t=5
group mean:  13.67    10.33     8.33     5.0      3.0
A (all-C):   +1.33    +1.67    +0.67    +1.0      0
B (D at 3):  −0.67    −0.33    −1.33    −2.0      0
C (D at 1):  −0.67    −1.33    +0.67    +1.0      0
```

B's defection at turn 3 is judged by rounds 3–5 only and gets the most negative t3 term,
in exactly the context where it happened. Credit assignment is solved in two halves:

1. **The past cannot punish a decision** — r1, r2 do not appear in G3; they are in
   sample 3's *prompt*, as state. A recovery after a bad start is scored on the
   recovery, not the start.
2. **The future is localized by same-turn comparison** — each decision competes only
   against alternatives made at the same point with the same remaining horizon.

## 2.4 The policy update: a sum of independent per-sample terms

After splitting, the batch is just **15 independent rows** — the same machinery as
training on 15 unrelated prompts. No episode bookkeeping survives to update time; the
episode structure was fully consumed when the Gt's and groups were computed.

```
row 1:  (prompt=[P],            response=C,  weight=+1.33)     ← from A, turn 1
row 2:  (prompt=[P],            response=C,  weight=−0.67)     ← from B, turn 1
row 3:  (prompt=[P],            response=D,  weight=−0.67)     ← from C, turn 1
row 4:  (prompt=[P a1 u2]_A,    response=C,  weight=+1.67)
...
row 15: (prompt=[P ... u5]_C,   response=C,  weight= 0   )

L(θ) = − (1/15) Σ_s  A_s · log π_θ(a_s | prompt_s)
```

Each term pushes its action up (A_s > 0) or down (A_s < 0) *in its specific context*,
proportionally to |A_s|. The optimizer sums all 15 gradients (ordinary minibatch SGD)
and takes **one step**. "How do I weight the 5 turns?" answers itself: the advantage IS
the weight, and the sum does the combining — turn 3's term neither waits for nor
modifies turn 5's.

Zero advantages (turn 5: identical G's) contribute no gradient — correct, since the
data contains no evidence about which turn-5 action is better. In real GRPO the
log-prob term is the PPO clipped ratio `min(ρ·A, clip(ρ,1±ε)·A)` with `ρ = π_θ/π_old`,
A_s is z-scored (÷ group std), and each A_s applies to all tokens of that sample's
response — same structure, same reading.

## 2.5 Is the early-turn down-weighting the trajectory-level flaw? No.

In the table, B's turn-1 **cooperation** gets −0.67 (its G1 contains B's own later
defection). Looks like the Scheme-A disease — but watch what the sum does. All three
turn-1 samples share the *identical* prompt [P], so their pushes land on the same
distribution π(·|s1) and merge:

```
push on log π(C|s1):  +1.33 (A)  − 0.67 (B)  =  +0.67   → C pushed UP
push on log π(D|s1):  −0.67 (C)              =  −0.67   → D pushed DOWN
```

B's "unfair" negative is **outvoted inside the sum** — the net signal at s1 is correct.
As group size grows, the C-samples' mean G converges to the value of playing C at s1
(with its downstream consequences) and the D-samples' to the value of D: the comparison
becomes Q(s1,C) vs Q(s1,D). Trajectory-level has no such per-decision convergence — no
matter how many rollouts, every decision keeps receiving its episode's single number.

The crispest contrast is episode C (defect first, then recover):

```
                        trajectory-level GRPO      per-turn split
C's round-1 D:                −0.67                    −0.67
C's round-3 recovery C:       −0.67  ← punished!       +0.67  ← rewarded
C's round-4 C:                −0.67  ← punished!       +1.0   ← rewarded
```

Trajectory-level punishes the recovery *every time* it occurs in a below-average
episode — systematic, and it directly suppresses "return to cooperation after
conflict". Per-turn split rewards it on its own future. The difference is **systematic
mis-crediting vs zero-mean noise**.

Where noise genuinely remains: B's round-4 recovery gets −2.0 — it cooperated while
absorbing TFT's retaliation (S=0), and the turn-4 group compares it against A and C who
sit in the friendly state (C,C). This situation-mixing noise averages out only
partially with group size; it is exactly what **GiGPO** removes by grouping on the
state instead of the turn index —

```
turn-index group:  "all turn-4 decisions"        ← mixed situations, noisy
GiGPO group:       "all decisions made at (D,C)" ← same situation, fair
```

so B's turn-4 sample competes against other decisions from (D,C) (e.g. C's turn-2),
where "cooperate while being punished" faces its true alternatives. Same loss formula;
only the group defining A_s changes. This is the tabular per-state question Tennant's
Q-learner answers, recovered critic-free; with only 4 memory-1 states the groups pool
across episodes AND turns and become huge. (Technicality: G's from different turns have
different remaining horizons; GiGPO handles this with discounting — minor at 5 rounds.)

Bottom line — both schemes are noisy estimators; the difference is *what kind* of error:

| | trajectory-level | per-turn split (Gt) |
|---|---|---|
| past contaminates a decision | yes, always | **never** (past is in the prompt) |
| recovery in a bad episode | punished **systematically** | rewarded on its own future |
| identical early actions, different futures | same shared number regardless | noise; **cancels in the sum**, converges to Q-comparison |
| leftover weakness | everything | situation mixing at t≥2 → GiGPO grouping |

## 2.6 SDPO: "wrap-first" vs "split" form

It is **the same splitting operation as for GRPO** — one data-prep step serves both.
The difference between the two forms is where the moral value sits relative to the
decision being distilled.

**Wrap-first (current):** one sample = the whole conversation; the teacher's copy
differs only in message 1:

```
student: [P              a1 u2 a2 u3 a3 u4 a4 u5 a5]
teacher: [wrap(P, value) a1 u2 a2 u3 a3 u4 a4 u5 a5]
                                        ↑ by here, teacher ≈ student → no signal
```

By round 3 the only difference between the two contexts is one distant early message
buried under identical transcript; the two distributions converge and the distillation
gradient vanishes — the wrap-first decay the signal-decay probe measured.

**Split form:** the same 5 prefix-samples, but each sample's prompt gets the value
wrapped in individually:

```
sample 3, student: [P              a1 u2 a2 u3] → distribution over a3
sample 3, teacher: [wrap(P, value) a1 u2 a2 u3] → distribution over a3
```

Every round gets its own distillation comparison as its own sample, teacher-forced on
just that round's decision. Wrap placement also becomes a free per-sample choice — the
value could go into the *most recent* user message, right next to the decision (the
eval's `wrap_position` knob already anticipates this); the decay probe says proximity
matters, so split + near-decision wrapping is the configuration to test.

**Summary of the whole chain:**
rollout → split into per-turn samples → Gt per sample (future only) → group-relative
A_s (turn-aligned, or state-grouped à la GiGPO) → sum of per-sample clipped-PG terms →
one update. GRPO gets per-turn credit out of the split; SDPO gets per-turn teacher
proximity out of the *same* split.

---

# Part 3 — Handoff: multi-turn implementation investigation (next session)

**Goal:** work out how to implement per-turn splitting (Scheme B) in the MoralGymVerl
verl stack — investigation first, then implementation plan, then code.

**Read first:** this doc (Parts 1–2 are the settled design and rationale).

**Relevant code entry points:**
- `src/moralgym_verl/training/game_interaction.py` — multi-turn rollout driver
  (BaseInteraction); already computes exact per-round rewards (`turn_scores`); builds
  the full `build_prompt(...)` as every env message (the "lean env message" change
  starts here).
- `src/moralgym_verl/training/reward_manager.py` — currently sums `turn_scores` into
  one scalar at the last token (Scheme A). Splitting changes what this produces.
- verl's agent loop: `verl/experimental/agent_loop` / `tool_agent_loop.py` (response
  pool, `response_mask` with user-turn 0s) — where the recorded transcript lives.
- SDPO fork: `~/SDPO/verl/trainer/ppo/ray_trainer.py` `_build_teacher_message` (~line
  710) — wrap-first teacher construction to be adapted to per-sample wrapping.
- Eval-side parity pieces to reuse: `eval/teacher_context.py` (wrap), the
  `wrap_position` knob in `eval/generation.py`.

**Open engineering questions to investigate:**
0. FIRST, the restatement flaw (see "The restatement flaw, sharpened" in Part 1): run
   the cheap shortcut-hypothesis check on existing paired transcript/stateless cells,
   then implement `build_env_message` in eval + `game_interaction.py` in lockstep.
   This is independent of, and smaller than, the splitting work — do it first so the
   lean-message format is settled before split samples bake prompts in.
1. Where to hook the split: post-rollout, before DataProto batching. Does verl (our
   pinned version) have any native turn-level/step-level sample support, or do we
   splice the agent-loop output ourselves? Check existing implementations built on
   verl: GiGPO's official repo (verl-agent), RAGEN, MT-GRPO reference code — steal the
   integration pattern, not necessarily the algorithm.
2. Token bookkeeping: per-turn samples = prefix token ids (prompt) + that turn's
   response ids. Prefixes are shared — memory/compute tradeoff (recompute vs reuse;
   packing). Also: split samples from the same episode are correlated — does verl's
   GRPO grouping (uid-based) need a per-turn uid scheme?
3. Reward-to-go: compute Gt from `turn_scores` at split time (trivial); decide
   discount γ (1.0 vs ~0.95) and document.
4. Grouping: turn-aligned first (uid = episode-group × turn). GiGPO state-grouping
   upgrade: anchor key = (agent_prev, opp_prev); needs horizon normalization across
   turns — check how the GiGPO paper/repo handles it.
5. SDPO split form: teacher prompt per sample = wrap the value into the sample's
   prefix; decide wrap position (message 1 vs most recent user message — decay probe
   says proximity matters, so near-decision is the configuration to test) and keep
   eval `wrap_position` parity.
6. Lean env messages: design the minimal next-round message (opponent move, payoffs,
   "choose"); decide whether game rules repeat (deliberate choice — see Part 1); keep
   eval transcript mode in lockstep (`make_chat_policy_fn`) so eval/training parity
   holds.
7. Known constraints to respect: `success_reward_threshold` scaling (reward_manager
   NOTE), response-pool sizing in `sdpo_pd_tft.yaml` (max_response_length comment),
   echo-trap monitoring (entropy/variance) per RAGEN.

**Validation plan (before any long training run):**
- Unit: split of a synthetic 5-turn transcript → exact expected (prefix, response, Gt)
  triples, illegal-move rounds handled (state-freeze convention as in eval/scoring).
- Parity: sum of split-sample Gt-weighted... simplest invariant: turn-1 sample's G1 ==
  reward_manager's summed episode reward.
- Smoke: 10-step GRPO run, split pipeline vs Scheme A on identical rollout seeds —
  losses finite, advantages match hand-computed values on a logged batch.
- Eval: stage1b_transcript before/after short training run (the eval side is DONE —
  see docs/teacher_signal_eval.md and the behavioral pipeline).

# Part 4 — SOTA check: multi-turn self-distillation (2026-08-10 web review)

*(Condensed findings of a literature pass over the 2026 multi-turn OPD/self-
distillation thread, and what they change — or confirm — about Parts 1–3.
Full sources in the references below.)*

## 4.1 The field independently found our flaws — and mostly our fix

**RL side (Flaw 1):** turn-level reformulation is the converged answer.
MT-GRPO and Turn-PPO recast the episode as a turn-level MDP with per-turn
advantages; GiGPO adds step-level grouping. Scheme B is this, not a
homegrown detour.

**Distillation side (Flaw 2):** multi-turn on-policy self-distillation is
its own 2026 research thread, and it documents our decay as a general
phenomenon: as turns accumulate, the teacher assigns progressively lower
probability to student tokens and supervision degrades ("multi-turn OPSD
instability", SDAR; OPD survey). Prefix Replay is the direct precedent
for the split on this side too: one training row per decision, history in
the prompt, teacher supervision attached at the decision point — claimed
to fix exactly signal dilution, credit assignment, and the context
mismatch. (Caveat: their teacher also gets hindsight/future information;
ours does not.)

**Not investigated there:** Skill-SD and SDAR — the two closest
*self*-distillation works (teacher = same model + privileged context) —
do NOT split. They keep the whole-trajectory masked row and patch it
in-place (importance-weighted losses, token-level gap-gating, teacher
sync). Note why they can: their privilege (retrieved skills) stays
informative at every turn, so teacher/student contexts never fully
converge. Our privilege is a static principle in message 1 — decay is
structural for us in the unsplit form, which makes the split MORE
necessary here than in their settings.

## 4.2 What transfers directly (actionable)

1. **Keep student rollouts plain and on-policy** — validated twice over:
   teacher-generated rollouts collapse training, and prepending the
   privileged text to the *student* prompt causes train-test-mismatch
   overfitting (Skill-SD). Our SDPO already does both correctly.
2. **Divergence direction is a live knob:** SDAR's ablation found reverse
   KL substantially beats Jensen-Shannon in multi-turn agentic
   distillation (mode-seeking: only teacher-endorsed tokens are
   incorporated). Our `generalized_jsd` alpha already implements the
   reverse-KL branch (alpha=1 -> KL(s||t)) — a zero-code Phase-4 ablation
   arm the literature now specifically motivates.
3. **Gap-gating** (distill hard only where the teacher confidently
   disagrees, SDAR) is the candidate remedy if late-round signal is weak
   even after wrap-latest.
4. **SDAR is the existence proof that GRPO and self-distillation combine
   on shared rollouts** — indirect support for our comparability design
   (same rollouts, same split rows, two separated loss heads).

## 4.3 What the split does and does not fix — the mirror image

Naive multi-turn: **GRPO has a strong but unattributed signal** (one
scalar smeared over all decisions); **wrap-first SDPO has an attributed
but vanishing one** (pointwise loss, but teacher/student contexts
converge, gradient -> 0 in late rounds). Credit assignment and decay are
different axes: SDPO never has the attribution problem (dense pointwise
supervision), decay is a signal-STRENGTH problem.

Consequently the split alone fixes Flaws 1/3/4 but NOT decay — a split
row with wrap-first still buries the value at the top of a long prompt.
Decay is fixed by **split + wrap-latest** (value adjacent to every
decision). This is why the wrap-position probe ablation is the one
pre-Phase-4 experiment that tests the component the split does not
automatically deliver.

## 4.4 The gap that stays ours

Nobody in this literature uses a static normative principle as the
privileged context, and nobody trains in strategic games against
adaptive opponents — privilege is always dynamic and task-instrumental
(retrieved skills, correct solutions, hindsight), objectives are task
success. "Multi-turn self-distillation of moral principles in social
dilemmas" is unclaimed; every ingredient of our design now has
independent published support, but the combination does not.

## References (from the preceding literature review)

- Turn-Level Credit Assignment (ICML 2025): https://arxiv.org/abs/2505.11821
- GiGPO — Group-in-Group Policy Optimization: https://arxiv.org/abs/2505.10978
- RAGEN / StarPO (echo trap, rollout diversity): https://arxiv.org/abs/2504.20073
- ArCHer — hierarchical multi-turn RL: https://arxiv.org/abs/2402.19446
- SWEET-RL — turn-level critic: https://arxiv.org/abs/2503.15478
- Response-Level Rewards Are All You Need (counterpoint): https://arxiv.org/pdf/2506.02553
- Fireworks best practices for multi-turn RL: https://fireworks.ai/blog/best-practices-for-multi-turn-RL
- In-context co-player inference (IPD cooperation mechanism): https://arxiv.org/html/2602.16301

### 2026 self-distillation / multi-turn OPD thread (Part 4 sources)

- SDPO — RL via Self-Distillation (the algorithm our fork implements):
  https://arxiv.org/abs/2601.20802 (project: https://self-distillation.github.io/SDPO)
- Skill-SD — skill-conditioned self-distillation, multi-turn agents
  (OPSD failure-mode catalogue): https://arxiv.org/abs/2604.10674
- SDAR — Self-Distilled Agentic RL (gated GRPO+OPSD hybrid; reverse-KL >
  JSD ablation; gap-gating): https://arxiv.org/abs/2605.15155
- Prefix Replay — multi-turn OPD with one row per decision (the split's
  distillation-side precedent): https://arxiv.org/abs/2607.04763
- Guided-OPD — curriculum turn-level teacher guidance: https://arxiv.org/abs/2606.15912
- Turn-PPO — turn-level MDP + turn-level critic: https://arxiv.org/abs/2512.17008
- OPD survey (documents turn-wise teacher-probability decay):
  https://arxiv.org/abs/2604.00626
- TCOD / ATOD — temporal-curriculum OPD variants: https://arxiv.org/abs/2604.24005,
  https://arxiv.org/abs/2606.27814
- SAGE-OPD — selective turn-level intervention + confidence weighting:
  https://arxiv.org/abs/2606.19659
- Privileged Information Distillation: https://arxiv.org/abs/2602.04942
- Credit-assignment survey (reasoning -> agentic): https://arxiv.org/abs/2604.09459
