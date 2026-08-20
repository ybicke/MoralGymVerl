# Multi-turn today: how it works, and its four flaws

*(2026-08-08. Plain-language walkthrough of what actually happens to a multi-turn
episode, from rollout to gradient, and where it breaks. Companion to
`multi_turn.md` (the design), `multi_turn_implementation_plan.md` (the build
plan), and `handoff_multiturn_eval_review.md` (next session's task).)*

Running example throughout — a 3-round Prisoner's Dilemma against tit-for-tat,
payoffs T=4, R=3, P=1, S=0:

```
u1: [rules + payoff matrix + "choose"]                    ← the initial prompt
a1: "...reasoning... Action: action2"        (defect)
u2: "A chose action1: you got 4 points and A got 0"       ← env message
a2: "...reasoning... Action: action1"        (cooperate)
u3: "A chose action2: you got 0 points and A got 4"       ← env message
a3: "...reasoning... Action: action1"        (cooperate)
```

`u` = user/environment messages, `a` = what the model wrote.

---

## Part 1 — What happens today, step by step

### Step 1. The rollout is one conversation

The model plays the whole episode as a single ongoing chat. Every turn it
re-reads everything above. Nothing is re-prompted or replayed; the history is
simply *there*, in context.

(Since Phase 1, the env messages `u2`/`u3` are minimal — the opponent's move,
the payoffs, and the answer-format line, ~30 tokens. They used to repeat the
whole rulebook, ~350 tokens.)

### Step 2. The whole episode is stored as ONE training row

verl records the conversation as a prompt plus one long response:

```
prompt   = [u1]
response = [a1][u2][a2][u3][a3]
```

Everything after the initial prompt — including the environment's own
messages — counts as "the response". That is the design decision from which
most of the flaws below follow.

### Step 3. A mask marks which response tokens are the model's

Because the environment's text sits inside the response region, a mask records
who produced what:

```
response:      [a1]  [u2]  [a2]  [u3]  [a3]  [pad]
response_mask:  1     0     1     0     1     0
                ↑ model's own tokens    ↑ env's tokens / padding
```

**Why the zeros exist:** the loss must only act on tokens the model *chose*.
If you trained on `u2` as well, you would be raising the probability of the
text *"A chose action1: you got 4 points"* — teaching the model to write the
opponent's turn itself (it then hallucinates the game at rollout time), and
telling it to make favourable opponent behaviour more likely, which it cannot
control. So: **don't take credit or blame for tokens you didn't write.**

Crucially, this is a *loss* mask. Those same env tokens must remain fully
**visible** — they are the game state the reasoning depends on.

```
env tokens →  loss: EXCLUDE     attention: INCLUDE
padding    →  loss: EXCLUDE     attention: EXCLUDE
```

In single-turn there are no env tokens inside the response, so the mask's zeros
mean *only* padding, and both columns agree. That coincidence is what Flaw 3
below trips over.

### Step 4. Rewards are collected per round, then summed

`GameInteraction` scores each round as it happens (`turn_scores = [4, 0, 3]`),
and `MoralGymRewardManager` sums them into **one number** placed at the last
token: `7`.

### Step 5. GRPO turns that into one advantage

The episode's score is compared against the other rollouts of the same prompt
(the group), z-scored, and the resulting single number is applied to **every**
response token of that episode.

```
episode total 7 → advantage −0.4 → applied to a1, a2 AND a3 alike
```

### Step 6. SDPO adds a teacher pass

SDPO's teacher is *the same model with better context* — the prompt rewritten
to include the moral value (and, when available, a successful sibling's
solution). It builds a second sequence:

```
student sequence:  [u1]              [a1][u2][a2][u3][a3]
teacher sequence:  [wrap(u1)]        [a1][u2][a2][u3][a3]
                    ↑ only this differs   ↑ student's tokens reused verbatim
```

Both are run through the model, and the per-token distributions are compared
(JSD, `alpha: 0.5`); the student is pulled toward the teacher.

Reusing the student's own tokens (rather than sampling from the teacher) is
deliberate and correct: the goal is to match *distributions*, which requires
evaluating both at the same points. This is standard on-policy distillation,
not a shortcut.

---

## Part 2 — The four flaws, in the order they appear above

### Flaw 1 — One advantage for five decisions (arises at Step 5)

Every decision in the episode receives the episode's single number.

```
a1 (defected — the mistake)      advantage −0.4
a2 (cooperated — good recovery)  advantage −0.4   ← punished for a1's mistake
a3 (cooperated — good)           advantage −0.4   ← punished for a1's mistake
```

The learner can't tell which decision was responsible, so it must stumble on
whole good episodes by chance. Worse, it *systematically* punishes recovery
after a bad start — exactly the "return to cooperation after conflict"
behaviour the research is about.

**Severity:** fundamental. This is the main motivation for per-turn splitting.
**Status:** affects any multi-turn GRPO run, present and past.

### Flaw 2 — The teacher signal decays (arises at Step 6)

The teacher and student contexts differ **only in message 1**, and everything
after it is identical and growing.

```
deciding a1:   teacher context = [wrap(u1)]              ← value is right there
               student context = [u1]                       → big difference

deciding a3:   teacher = [wrap(u1)] a1 u2 a2 u3          ← value is 4 messages back
               student = [u1]       a1 u2 a2 u3          → nearly identical contexts
                                    └──── identical ────┘
```

By round 3 both versions condition on the same recent material and produce
nearly the same distribution, so the distillation gradient ≈ 0. The value is
technically present but no longer moves the decision. Measured previously: the
signal largely dies after round ~2.

**Severity:** makes multi-turn SDPO ineffective beyond the first rounds.
**Note:** Phase 1 shrank the intervening text ~10× (350→30 tokens per env
message), so the curve must be re-measured — this is the review's job.

### Flaw 3 — The teacher is blinded to the game state (arises at Step 6)

`~/SDPO/verl/trainer/ppo/ray_trainer.py:763` builds the teacher's **attention**
mask by reusing the **loss** mask:

```python
teacher_attention_mask = torch.cat([teacher_prompt["attention_mask"], response_mask], dim=1)
```

In single-turn that is correct — the zeros are only padding. In multi-turn the
zeros now also cover the env messages, and attention-zero means *"this token
does not exist"*:

```
student sees:  u1  a1  [A chose action1, you got 4]  a2  [A chose action2, you got 0]  a3
teacher sees:  u1' a1  ........blocked........        a2  ........blocked........       a3
```

The teacher is asked what to play in round 3 while unable to see anything the
opponent did. Its distribution is therefore meaningless, and whatever gets
distilled from it is noise.

**Severity:** correctness bug; would silently corrupt any multi-turn SDPO run.
**Location:** the SDPO fork's training code (core SDPO, not MoralGym code). Our
eval does **not** have it — `probe_b` builds contexts as message lists with
full attention, so the probe measures a cleaner teacher than training would
actually use.
**Status: never executed.** `sdpo_pd_tft.yaml` has no multi-turn blocks, so
multi-turn SDPO has never run. No existing result is affected. All SDPO results
to date are single-turn, where the line is correct.

### Flaw 4 — The teacher is judged on a history it wouldn't have produced (mild)

Rounds 2+ contain the *student's* actions and the opponent's replies to them.
A value-following teacher might never have defected in round 1, so it is being
asked about a game state its own policy wouldn't reach.

This is the mildest of the four and is really a framing problem: it is
well-posed as soon as you stop claiming "this is the teacher's trajectory" and
instead ask "at this position, what does the value-conditioned policy do?" —
like asking a chess coach what they'd play in a given position without their
having played the opening. Per-turn splitting makes exactly that reframing
explicit.

---

## Part 3 — What per-turn splitting changes

The rollout stays identical. Afterwards, the one recorded row is cut into one
row per decision — pure bookkeeping on the recording, no replay:

```
BEFORE (1 row)
  prompt = [u1]   response = [a1][u2][a2][u3][a3]

AFTER (3 rows)
  sample 1:  prompt = [u1]                  response = [a1]
  sample 2:  prompt = [u1 a1 u2]            response = [a2]
  sample 3:  prompt = [u1 a1 u2 a2 u3]      response = [a3]
```

Each row now looks like an ordinary single-turn sample. What that buys:

**Fixes Flaw 1.** Each decision gets its own reward — the *reward-to-go*, i.e.
this round and everything after it, never what came before:

```
G1 = 4+0+3 = 7      a1's own consequences
G2 =   0+3 = 3      a2 judged on rounds 2-3 only — the round-1 defection is in
G3 =     3 = 3      its PROMPT (as state), not in its reward
```

Decisions are then compared against decisions made at the same point in other
rollouts. A recovery after a bad start is scored on the recovery.

**Fixes Flaw 3 structurally.** Sample 3's response is `[a3]` alone — one
assistant span, so its `response_mask` is all 1s and the loss/attention
distinction collapses. The env messages `u2`, `u3` now live in the *prompt*,
where both passes attend to them normally. The shape that caused the bug no
longer exists.

**Fixes Flaw 2.** Because `u3` is now the last message of the prompt, the value
can be attached right next to the decision:

```
sample 3, student prompt:  [u1, a1, u2, a2,      u3 ]
sample 3, teacher prompt:  [u1, a1, u2, a2, wrap(u3)]
                                             ↑ adjacent to the decision — nothing to bury it
```

**Resolves Flaw 4.** Each sample is now literally a (state → action) pair, so
the per-position query is the honest reading rather than a reinterpretation.

---

## Part 4 — Status and what to decide next

| flaw | severity | affects existing results? | fixed by |
|---|---|---|---|
| 1. one advantage for all decisions | fundamental | yes, any multi-turn GRPO | per-turn split (Phase 2–3) |
| 2. teacher signal decay | high (SDPO) | no — never run multi-turn | split + near-decision wrap (Phase 4) |
| 3. teacher blinded to env tokens | correctness bug | **no — never executed** | split (structurally); or a 1-line mask fix |
| 4. counterfactual history | mild / framing | n/a | split (reframing) |

**Already done (Phase 1, commit `bf4f9f3`):** env messages reduced to the new
information only; multi-round eval is conversation-only; training/eval/probe
build identical messages.

**Open questions for the review session:**

1. Does the decay measurement (Flaw 2) still hold now that env messages are 10×
   shorter? Must be re-measured.
2. `probe_b.play_episode` hardcodes wrap-first, so the wrap-first vs
   wrap-latest comparison — the experiment that would validate the Phase-4
   near-decision wrap *before* building it — cannot currently be run. ~10-line
   fix.
3. Note the eval/training divergence introduced by Flaw 3: the probe measures a
   teacher that sees the game; training would use one that doesn't. Any
   pre-split comparison between the two is apples-to-oranges.
4. Decide whether to fix Flaw 3 directly (one line: use the response attention
   mask, not the loss mask) as insurance, even though the split makes it moot —
   cheap, and prevents a stale trap if anyone runs multi-turn SDPO before the
   split lands.

**Then:** Phase 0 (record per-turn token spans) → Phase 2 (the split transform
+ unit tests) → Phase 3 (GRPO smoke) → Phase 4 (SDPO split form) → Phase 5
(GiGPO state grouping, deferred). Details in
`multi_turn_implementation_plan.md`.
