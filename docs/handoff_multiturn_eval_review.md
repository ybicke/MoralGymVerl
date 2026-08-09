# Handoff — Phase 1 landed; review the multi-turn eval next

*(2026-08-08. Written at the end of the Phase-1 session for the next session.
Companion docs: `multi_turn.md` = settled design; `multi_turn_implementation_plan.md`
= the full build plan, Phase 1 marked DONE.)*

## 1. What landed (commit `bf4f9f3`, 14 files, +354/−51, 83 tests green)

### 1.1 The protocol change

Multi-round episodes are now **one accumulating conversation**, and the per-round
env message carries only new information.

```
BEFORE (every round, ~350 tok)          AFTER (rounds >= 2, ~30 tok)
full rules + payoff matrix              "A chose action1: you got 3 points
+ "You have played with A before,        and A got 3 points."
 last round you played X..."            + [round clause, only if show_horizon]
+ closing question                      + answer-format line
```

Round 1 is unchanged — including the fabricated-seed narration for
`game_design: hist`, which *must* stay because a mid-game entry state cannot be
in a context that has no prior turns. The guiding rule, stated once so it can be
applied to future cases: **narrate history exactly when it is not in context.**

Note (corrects an overstatement made mid-session): dropping the last-round
summary removes *redundancy*, not information. Any outcome report necessarily
encodes the full memory-1 state, because the payoff pair identifies both moves.
So this change is not a shortcut fix; the argument for it is token cost,
redundancy, and the fact that the summary existed only because the stateless
prompt was reused as an env message by accident.

### 1.2 New API — `build_env_message` (`game/prompts.py:266`)

```python
build_env_message(config, agent_action=None, opp_action=None, round_idx=None) -> str
```

- `agent_action`/`opp_action` `None` → no outcome line. That is the
  **illegal-round** case: state frozen, nothing to report; callers prepend
  `parse_failure_feedback(config)` themselves.
- `round_idx` is the 1-indexed round about to be played; the round clause is
  emitted only under `show_horizon` (same pairing as `build_prompt`).
- Routes the closer internally: reasoning configs get
  ``end with `Action: X` or `Action: Y` ``, standard configs get the
  "choose either X or Y … Your answer:" form (honoring `minimal_parsing`).
- The answer-format line is repeated **every** round deliberately: format drift
  (unparseable output) is the first multi-turn failure mode in small models, and
  this is the cheap "recitation" that guards against it.

### 1.3 New flag — `restate_rules_per_round` (default `False`)

`EpisodeConfig` field (`game/environment.py:154`), plumbed through
`training/dataset.py` (config + ground-truth state dict) and
`eval/config.py:build_eval_config`. `True` re-inserts the payoff block + the
closing question into every env message — no opener, no history sentence.

This is the **ablation arm for weaker models** (planned: Gemma / Mistral 7–9B,
likely reasoning variants). Rationale for keeping it as a real arm rather than
hardcoding either behavior: multi-turn instruction decay is documented
(Laban et al. 2025 measured ~39% single→multi-turn degradation; position-bias
work points the same way) and is stronger for small models and long reasoning
traces — but at 5 rounds and <4k tokens we are in mild territory, so it is an
empirical question, not a settled one. Declared in
`configs/datasets/grpo_pd_tft_mt.yaml`.

### 1.4 Stateless multi-round eval — **deleted** (user decision)

Multi-round means conversation, full stop. Removed: the `evaluation.conversation`
config key, the `--conversation` CLI override, the `multi_round_conversation`
preset, and the `conversation` field in run metadata.

`PROTOCOL_PRESETS` is now:

| preset | rounds | design | policy |
|---|---|---|---|
| `single_round` | 1 | `hist` | one-shot stateless (unchanged) |
| `multi_round` | 5 | `nohist` | conversation, env messages from round 2 |

`build_policy` selects the chat-accumulating policy iff `num_rounds > 1`
(`eval/behavioral.py:135`) — the mode is now derived from the protocol instead
of being independently switchable, so the two can no longer disagree.

Old protocol-name mapping, for reading pre-2026-08 results:
`stage1a` = `single_round`; `stage1b` = the deleted stateless multi-round
protocol; `stage1b_transcript` = today's `multi_round`.

### 1.5 Three call sites in lockstep

| site | file | role |
|---|---|---|
| training | `training/game_interaction.py:241` (legal), `:186` (illegal) | verl rollout |
| eval | `game/trajectory.py:run_episode` (`rnd > 0` branch) | behavioral eval |
| probe | `eval/probe_b.py:play_episode` | teacher-signal probe |

Each tracks `prev_agent`/`prev_opp`, resetting both to `None` on an illegal
round. `tests/test_env_message.py` pins byte-equality between
`GameInteraction.generate_response` and `build_env_message`, including the
illegal-round composition, so the three cannot silently drift.

### 1.6 Test suite

New `tests/test_env_message.py` (8 tests): builder content, no-outcome case,
round clause ↔ `show_horizon` pairing, reasoning closer, `restate_rules` arm,
`run_episode` round-2 message, illegal-freeze composition, and
training/eval parity via a real `GameInteraction` episode.
`test_probe_episode.py` updated (round-2 assertion now expects
feedback + env message); `test_behavioral_metadata.py` and
`test_evaluate_integration.py` dropped their `conversation` assertions.
Full suite: **83 passed**.

Also in this commit: two stale docstrings in `game_interaction.py` fixed (they
claimed no trainer path summed `turn_scores` — `MoralGymRewardManager` does).

## 2. What is and isn't affected

**Changed — re-run needed, old results not comparable:**
1. `multi_round` behavioral eval (was `stage1b_transcript`) — round-≥2 context differs.
2. `probe_b --states episode` (`probe_b_episode`) — same.
3. The deleted stateless multi-round protocol — not runnable from HEAD at all.

**Untouched — reproducible, results stand:**
4. `single_round` behavioral eval (stage1a per-state policy table).
5. `probe_b --states fabricated` (the four memory-1 states).
6. Probe A.
7. All scoring / statistics / plotting (`iter_decisions`, regret, JSD, sweeps) —
   input formats unchanged.

Rule of thumb: **single-step is untouched; multi-round changed or was removed.**

## 3. The open question for next session: does the decay probe still earn its place?

The user is unconvinced by the `probe_b` episode-mode "signal decay" measurement
and wants it understood and reassessed before more is built on it. Below is
what it actually does, plus the specific reasons it is now questionable.

### 3.1 What it measures, mechanically

`probe_b --states episode` runs in two phases (`play_episode` → `score_rounds`):

1. **Rollout.** Play one real multi-round episode with the **plain** student
   prompts. Store each round's trace and the message list so far.
2. **Scoring.** For each round, teacher-force *the student's own trace* under two
   contexts that differ in exactly one place — the teacher's copy has the moral
   value wrapped into the **first** user message (`wrap_first_user`,
   `probe_b.py:194`, hardcoded). Then report per round:
   - `token_delta` — mean per-token logprob difference (teacher − student):
     distillation pressure on the reasoning.
   - `token_jsd` — full-vocab generalized JSD, i.e. the step-0 SDPO per-token loss.
   - `answer_delta` — label log-odds shift with the reasoning held fixed
     (trace truncated at its own `Action:` marker).

"Decay" = these deltas shrink as the round index grows. The prior finding was
that the signal is largely gone after round ~2, which is the empirical
motivation for split-form SDPO.

### 3.2 Why it is worth re-examining

1. **It may be closer to a mechanical fact than a behavioral finding.** The two
   contexts differ only in message 1 and share an ever-growing identical suffix.
   As shared tokens accumulate, the two distributions must converge. If so, the
   probe's value is in quantifying the *rate*, not in establishing the
   phenomenon — and the framing should say so.
2. **Phase 1 changed the very quantity that drives it.** The intervening text per
   round dropped ~10× (350 → 30 tokens). If decay is mostly "burial under
   intervening tokens", the curve should now be markedly flatter. Unknown until
   re-run — and this is a genuinely informative re-measurement, because it
   separates "burial by volume" from "recency/position effects".
3. **It measures a configuration the plan intends to abandon.** Wrap-first is the
   current trainer behavior; split-form SDPO (Phase 4) attaches the value next to
   each decision. So the probe as it stands measures the *old* design's weakness.
4. **Blocking limitation for the comparison that would matter:**
   `play_episode` hardcodes `wrap_first_user`. `eval/teacher_context.py` already
   provides `wrap_latest_user`, and `eval/generation.py` already has a
   `wrap_position` knob — but probe_b does not thread it. **The experiment worth
   running is wrap-first vs wrap-latest on the same episodes**: if wrap-latest
   shows no decay, that is direct evidence for the Phase-4 near-decision wrap,
   and it turns the probe from "documents a weakness we already decided to fix"
   into "validates the fix". That needs a small change to `play_episode`
   (thread `wrap_position`, choose the wrap fn per round).

### 3.3 Suggested shape for the review

- Explain the measurement from scratch to the user first; do not assume the
  prior framing is right.
- Decide: keep as-is / reframe as a rate measurement / extend to the
  wrap-first-vs-latest comparison / retire.
- Note that split-form SDPO is independently motivated by credit assignment
  (`multi_turn.md` §2.5), so retiring the probe would not undermine the plan.
- Then sweep the rest of the multi-turn eval surface for pieces whose rationale
  died with the stateless protocol.

## 4. Then: back to the build

Phases 0 and 2–5 are unwritten but fully specified in
`multi_turn_implementation_plan.md`:

- **Phase 0** — record per-assistant-span token boundaries + the final message
  list in `SDPO/verl/experimental/agent_loop/tool_agent_loop.py`, emit via
  `extra_fields`; create `sdpo_pd_tft_mt.yaml` from
  `grpo_pd_tft_mt.yaml:38-59`'s four multi-turn blocks.
  **Carry-over from Phase 1:** redo the `max_response_length` sizing comment —
  env messages are ~30 tokens now, not ~350 (worst case ≈ 5×512 + 4×30 ≈ 2680).
- **Phase 2** — `src/moralgym_verl/training/turn_split.py`, the token-level split
  + login-node unit tests. Must keep the `try/except ImportError` guard pattern
  so it tests outside the container.
- **Phase 3** — GRPO smoke + invariants (turn-1 `G1` == Scheme-A episode reward;
  hand-checked advantages against `multi_turn.md` §2.3).
- **Phase 4** — SDPO split form (needs per-turn `success_reward_threshold`
  scaling).
- **Phase 5** — GiGPO state grouping, deferred.

Section 6 of the plan doc lists five things to verify once in the code
(`num_repeat` on the GRPO path, how the moral value enters the training-side
teacher, solution injection for t ≥ 2, `_episode_feedback` scaling on a
one-element score list, and validation-loop key leakage).

## 5. Repo state

`bf4f9f3` on `main`, clean except two `eval_results/teacher_signal` file
deletions that predate this session and were deliberately left unstaged.
A follow-up commit removes leftover "transcript mode" phrasing from the new
docstrings (the term was rejected as unclear; the codebase term is
"conversation" / "multi-round episode").
