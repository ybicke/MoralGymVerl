# Per-turn splitting (Scheme B): implementation plan

*(2026-08-08 — investigation results + implementation plan. Design and rationale live in
`multi_turn.md` Parts 1–2; this doc answers Part 3's seven open questions and lays out
the phased build. Investigation sources: local code audit of `~/MoralGymVerl` +
`~/SDPO`, and clones of verl-agent (GiGPO), RAGEN, MT-GRPO, and vanilla verl HEAD.)*

## 0. Executive summary

- **No verl we can use has native turn-level samples** — not our fork (0.7.0.dev), not
  vanilla HEAD (0.9.0.dev). Everyone builds it themselves; verl swallows step-level
  samples happily once they are flat DataProto rows.
- **Chosen architecture: physical split, token-level, post-`union` in the trainer.**
  A pure function in MoralGymVerl slices each episode row into per-turn rows by token
  spans recorded during rollout — *no re-tokenization* (none of the reference repos
  achieve this; they all re-render text prefixes, which is exactly the re-tokenization
  seam our eval review flagged). A ~10-line gated hook in the SDPO fork's `fit()`
  calls it.
- **SDPO split form is nearly free**: `_build_teacher_message` already rewrites the
  *last* user message of a sample's message list. Give each split sample its message
  prefix as `raw_prompt` and the existing machinery produces near-decision
  ("wrap-latest") teacher prompts — the configuration the decay probe favors.
- Grouping is pure bookkeeping: GRPO groups by the `uid` string, so
  `uid = f"{episode_uid}:t{turn}"` implements turn-aligned groups with zero
  advantage-code changes. GiGPO state-grouping later = a different uid key.
- Real work items: turn-span recording in `tool_agent_loop.py`, the split transform +
  unit tests, batch-divisibility shim, per-turn `success_reward_threshold` scaling,
  lean env messages in training/eval lockstep.

## 1. What the investigation established

### 1.1 Local stack facts (audit of ~/MoralGymVerl + ~/SDPO)

- **Training runs the `~/SDPO` fork exclusively** (verl 0.7.0.dev, editable-installed
  from bind-mounted source at every job launch — `scripts/slurm/train_verl.sh:51-53`).
  We can and should modify verl internals directly; there is no pip verl anywhere.
- **Rollout path**: async agent loop → `ToolAgentLoop.run()`
  (`SDPO/verl/experimental/agent_loop/tool_agent_loop.py:134-209`) driving our
  `GameInteraction(BaseInteraction)`. `AgentData` accumulates `prompt_ids` (growing
  token sequence), `response_mask` (1 = assistant span, 0 = env message), `messages`,
  and `turn_scores` (appended per round from `generate_response`'s reward, :388).
  `turn_scores` reaches the trainer via `extra_fields` → `non_tensor_batch`.
- **Output contract**: strictly one `AgentLoopOutput` row per episode.
  `_postprocess` (`agent_loop.py:729-795`) emits DataProto keys `prompts` (left-pad),
  `responses` (right-pad), `response_mask`, `input_ids`, `attention_mask`,
  `position_ids`, optional `rollout_log_probs`; non-tensor `turn_scores`,
  `tool_rewards`, `raw_prompt` (the *original* prompt only — the full message list is
  not propagated), `__num_turns__`.
- **Trainer flow** (`SDPO/verl/trainer/ppo/ray_trainer.py`): per-dataset-row `uid`
  (:1625) → `gen_batch.repeat(n, interleave=True)` (:1634) → generate →
  `batch.repeat(n).union(gen_batch_output)` (:1686, **asserts equal lengths and
  deep-equal duplicated keys** incl. `raw_prompt`) → `_balance_batch` → old/ref
  log-probs → reward (:1785) → SDPO teacher build (:1787) → `compute_advantage`
  (:1832, GRPO groups purely by the `uid` string, group-of-1 is safe:
  `core_algos.py:314-316`).
- **Reward manager** sums `turn_scores` to one scalar at the last valid response
  token. GRPO then does `token_level_rewards.sum(dim=-1)` — placement within the row
  is irrelevant to the advantage.
- **Termination gotchas** (split must respect): (a) the response pool
  (`max_response_length`) is shared across turns *and counts masked env-message
  tokens* — episodes can truncate mid-game with `len(turn_scores) <` number of
  assistant spans; (b) `max_assistant_turns`/`max_user_turns` can terminate before
  `INTERACTING`, so the last span may have no reward; (c) the final done step appends
  an empty templated user message as trailing masked tokens.
- **SDPO multi-turn wrinkle the split fixes for free**: the teacher batch currently
  concatenates the student's whole multi-turn response and uses `response_mask` as
  teacher *attention* mask (:763) — env tokens attention-masked for the teacher,
  visible to the student. Post-split every response is one assistant span, all 1s.
- **`sdpo_pd_tft.yaml` is not yet multi-turn.** The four required blocks
  (`rollout.agent.default_agent_loop: tool_agent`, `rollout.multi_turn.*`,
  `data.return_raw_chat: true`, `reward_manager` importlib block) exist in
  `grpo_pd_tft_mt.yaml:38-59` and must be copied over.
- **No existing tests** cover `game_interaction.py`/`reward_manager.py`; new modules
  must keep the `try/except ImportError` guard pattern so they unit-test outside the
  container. `test_probe_episode.py` and `test_representation.py` break if prompt/env
  message construction changes without updates.
- Stale docstrings in `game_interaction.py:37-47` (claim no trainer path sums
  `turn_scores` / that the rollout engine sums them — both wrong; the reward manager
  sums them). Fix in passing.

### 1.2 External integration patterns (clones in scratchpad)

| | sample unit | where split | verl coupling | grouping | per-sample reward |
|---|---|---|---|---|---|
| **verl-agent** (GiGPO, verl-0.3.1 fork) | 1/env-step, fresh text prompt with history window | *during* rollout (`TrajectoryCollector`) | in-tree fork; new adv-estimator branch; custom reward manager; `adjust_batch` copy/delete shim | episode-group uid + anchor-obs cluster uuid within group | discounted Gt (γ≈0.95, backward pass driver-side) + episode total for the episode half |
| **RAGEN full** (StarPO, verl-0.6.1 submodule) | 1/episode, token masks per turn | token-level, turn boundaries by counting `<\|im_start\|>` | `RayPPOTrainer` subclass; rollout replaces `generate_sequences`; injects `rm_scores`, `loss_mask`, `uid` pre-built | env group id | per-turn reward on each turn-end token (feeds bi-level GAE) or summed |
| **RAGEN single_turn** | 1/turn, re-rendered text prefix | post-rollout | same | group id, GRPO baseline dedups by `(uid, episode_id)` | whole-episode return (no reward-to-go!) |
| **MT-GRPO** | 1/episode | n/a — token-segment advantage gating | none (TRL/verifiers) | TRL per-prompt | turn-adv gated to pre-tool-result tokens |

Transferable lessons: (1) flat DataProto rows are all verl needs — friction is batch
divisibility (both repos wrote a copy/delete `adjust_batch`), uid bookkeeping, and
baseline bias from variable turn counts; (2) reward-to-go is computed driver-side
from the per-step rewards before the reward stage; (3) **nobody shares prefix tokens
— all re-tokenize text prefixes.** Our token-level slicing avoids both the memory
excuse and the re-tokenization seam.

## 2. Architecture decision

### 2.1 Two viable routes

**Route A — "painted advantages" (no physical split).** Keep one row per episode;
custom advantage estimator computes per-turn Gt, z-scores within turn-aligned groups,
and broadcasts each turn's advantage onto that turn's span tokens (RAGEN-full /
MT-GRPO family). Because log-probs of span-t tokens in the full sequence equal those
in the prefix-split sample (causal attention, identical token ids), this is
**gradient-equivalent to physical splitting for GRPO** at ~1/3 the token cost.

**Route B — physical split (chosen).** Slice each episode row into per-turn rows
post-`union`; everything downstream is vanilla.

Why B: (i) SDPO is the headline, and Route A cannot express per-turn near-decision
teacher wrapping in a single teacher sequence — you'd need per-turn teacher rows
anyway, plus bespoke logit scattering, i.e. the hard part of B without its
simplicity; (ii) B reuses `_build_teacher_message` almost unchanged (§4.5); (iii)
per-decision logging/debugging falls out (each row *is* a decision — matches the
eval's `iter_decisions` world); (iv) matches the settled design's "15 independent
rows" model and the literature. Route A stays on record as a GRPO-only optimization
if token cost ever bites; the gradient-equivalence also gives us a validation
cross-check.

Token cost of B: prefixes replicate, ≈ O(T²/2). At 5 rounds with lean env messages
and ~512-token traces: ~2.5–3× the episode's tokens. Acceptable; mitigated further by
tight tensor widths (§4.2) and `use_dynamic_bsz` token-based micro-batching.

### 2.2 Split point: after `union`, before `_balance_batch`

`ray_trainer.fit()`, immediately after `batch = batch.repeat(n).union(gen_batch_output)`:

```python
if self.config.algorithm.get("turn_split", {}).get("enable", False):
    batch = split_episode_batch(batch, self.tokenizer, self.config.algorithm.turn_split)
```

- After `union` → no length-equality or `raw_prompt` deep-equality issues (we mutate
  a single merged batch).
- Before `_balance_batch` → the balancer distributes the variable-length split rows
  by token count as designed.
- Before old/ref log-prob computation → log-probs, reward, SDPO teacher, advantage,
  and the actor update all see ordinary single-turn rows. Recomputing old log-probs
  on split rows is *exactly* consistent with the rollout (same tokens, same causal
  positions).

The transform itself lives in MoralGymVerl
(`src/moralgym_verl/training/turn_split.py`, import-guarded), loaded the same way the
custom reward manager is — the fork change is just the hook + config plumbing.

## 3. Answers to the seven open questions

**Q1 — where to hook.** No native support anywhere; splice as §2.2. Steal from
verl-agent: driver-side reward-to-go + `adjust_batch` divisibility shim. Steal from
RAGEN: episode-aware baselines and echo-trap metrics. Ignore their text-prefix
re-rendering — we slice tokens.

**Q2 — token bookkeeping.** Rollout records exact span boundaries (§4.1); split row
t = `prompt_tokens + response[:start_t]` as prompt (left-pad), `response[start_t:end_t]`
as response (right-pad), `response_mask` all 1s, `attention_mask`/`position_ids`
rebuilt (`compute_position_id_with_mask`). Prefixes replicated, not shared — accepted
by all reference implementations; tight widths + dynamic batching contain it.
Correlated same-episode samples: fine for the *advantage* (grouping is turn-aligned
across episodes), and RAGEN's `(uid, episode_id)` dedup is only needed when episode
counts per group vary a lot — at fixed 5 rounds, group sizes stay ≈ n per turn; noted
as a later refinement, not needed now. uid scheme: `f"{episode_uid}:t{turn}"`.

**Q3 — reward-to-go.** `Gt = Σ_{k≥t} γ^(k−t) · turn_scores[k]` computed in the split
transform from the actual `turn_scores` list (which already includes illegal-round
penalties and may be shorter than `num_rounds` on truncation). **γ = 1.0 default**,
configurable: at 5 rounds discounting is second-order, and γ=1 preserves the doc's
worked examples and the parity invariant G1 == summed episode reward. (GiGPO used
0.95; we revisit when/if state-grouping needs horizon normalization.) Delivery to the
reward manager: overwrite each split row's `turn_scores` with the single-element list
`[G_t]` — the existing `MoralGymRewardManager` sum-then-place-at-last-token path then
produces Gt with zero changes.

**Q4 — grouping.** Turn-aligned first: uid string as above; `compute_grpo_outcome_advantage`
groups by uid, `num_repeat` is unused on the GRPO path (verify at code time), size-1
groups degrade to zero advantage safely. GiGPO upgrade later = swap the uid key to the
frozen-state anchor `(agent_prev, opp_prev)` — **must reuse `eval/scoring.iter_decisions`'s
state-freeze convention verbatim** (last legal pair, frozen across illegal rounds,
seeded from fabricated history). verl-agent's answer to cross-turn horizons: group on
*discounted* Gt and let group-mean subtraction absorb the rest; adopt γ≈0.95 when we
get there. Deferred to Phase 5.

**Q5 — SDPO split form.** Propagate the episode's full message list through
`extra_fields`; the split assigns each row `raw_prompt = messages[:m_t]` (the message
prefix ending in the env message before span t). Then the *existing*
`_build_teacher_message` — `system_messages = raw_prompt[:-1]`, rewrite
`raw_prompt[-1]` with the reprompt template — natively produces the **near-decision
wrap** ("latest"), the configuration the decay probe says to test. Wrap-first becomes
the ablation (small variant: wrap `raw_prompt[0]` instead; keep eval's
`wrap_position` naming). Two required fixes: per-turn `success_reward_threshold`
scaling (§4.5) and the solution-injection semantics check (§6).

**Q6 — lean env messages.** New builder `build_env_message(...)` returning only
{opponent's move, payoffs, "choose again"}; config flag on the interaction
(`env_message_style: full | lean`, default `full` until validated). Rules do **not**
repeat in lean mode (the deliberate choice per Part 1); the illegal-move
`parse_failure_feedback` prepend is kept in both modes. Change lands in
`game_interaction.py` and `game/trajectory.run_episode` **in the same commit**
(training/eval parity), with `test_probe_episode.py` extended to pin both styles.
Bonus: shrinks the shared response pool pressure and the split's O(T²) constant;
recompute the `max_response_length` sizing comment after.

**Q7 — constraints.** `success_reward_threshold`: becomes per-turn (§4.5).
Response-pool sizing: lean messages change the arithmetic — redo the
`sdpo_pd_tft.yaml:107-114` comment. Echo trap: adopt RAGEN's cheapest signals as
logged metrics — in-group reward std per turn-group and mean per-token entropy —
before considering any filtering.

## 4. Phased implementation

### Phase 0 — plumbing (fork + config, no behavior change)
1. `tool_agent_loop.py`: record per-span metadata in `AgentData` — for each assistant
   span: `(start, end)` in response-token coordinates and the message-list index just
   before it. Emit via `extra_fields["turn_spans"]` alongside a new
   `extra_fields["full_messages"]` (the final `agent_data.messages`). Invariant
   enforced at emit time: use only the first `len(turn_scores)` spans (drops the
   unreworded final span from turn-cap termination); clip span ends to
   `response_length` and drop fully-truncated spans *and their scores* consistently.
2. Copy the four multi-turn blocks from `grpo_pd_tft_mt.yaml` into a new
   `sdpo_pd_tft_mt.yaml` (leave the single-turn config untouched as the control).
3. Fix the stale `game_interaction.py` docstrings.

### Phase 1 — lean env messages (independent, ship first)
As Q6. Small, self-contained, and de-risks the token budget for everything after.

### Phase 2 — the split transform + unit tests (the core)
`src/moralgym_verl/training/turn_split.py`:
```
split_episode_batch(batch: DataProto, tokenizer, cfg) -> DataProto
```
- Per episode row: for each kept span t build prompt/response/input_ids/
  attention_mask/position_ids as §Q2; slice **every** response-aligned tensor
  (`rollout_log_probs` if present); copy non-tensors; set
  `turn_scores=[G_t]`, `uid=f"{uid}:t{t}"`, `raw_prompt=messages[:m_t]`,
  add `turn_idx`, `episode_uid`, `remaining_rounds` non-tensors.
- Tensor widths: computed tight per batch (max prefix len, max span len), not
  `P+R`/`R` — this is most of the memory win.
- Divisibility shim: verl-agent-style `adjust_batch` (random duplicate/delete to the
  required multiple), applied last, every adjustment logged (no silent truncation).
  Prefer `use_dynamic_bsz` so only the mini-batch count constraint remains — verify
  which constraints actually bind in our fork at code time.
- Unit tests (login-node, no verl — synthetic tensors + the ImportError-guard
  pattern): exact (prefix, response, Gt) triples for a synthetic 5-turn transcript;
  illegal-round episode (penalty in Gt, span still a sample); truncated episode
  (fewer scores than spans); trailing empty-user-message tokens excluded; γ<1 case;
  uid scheme; every-response-aligned-tensor sliced.

### Phase 3 — GRPO smoke + validation (before any SDPO work)
- Hook into `fit()` behind `algorithm.turn_split.enable` (default false).
- Invariants on a logged batch: turn-1 `G1 ==` Scheme-A episode reward (untruncated
  episodes); hand-computed advantages for a 3-rollout group match the worked example
  in `multi_turn.md` §2.3; losses finite.
- Cross-check vs Route A logic offline: per-turn advantages painted on the unsplit
  sequence must equal the split batch's advantages token-for-token.
- 10-step GRPO run, split vs Scheme A on identical seeds; log in-group reward std +
  entropy (echo-trap watch).

### Phase 4 — SDPO split form
1. Per-turn threshold: `success_reward_threshold` interpreted as **per-round** rate;
   `_collect_solutions_by_uid` compares `G_t ≥ rate × remaining_rounds(t)` using the
   `remaining_rounds` non-tensor. (Small fork change; keeps single-turn behavior when
   `turn_split` is off.)
2. Wrap position: default = existing last-message rewrite (near-decision); add
   `wrap_position: first` variant for the ablation; assert template parity with
   `eval/teacher_context.py` in a test (extend `test_teacher_context.py`).
3. Confirm the teacher batch is now a clean single-span concat (the `response_mask`-
   as-attention-mask wrinkle gone by construction).
4. Short SDPO run; then stage1b_transcript before/after (eval side is done —
   `docs/teacher_signal_eval.md`).

### Phase 5 (deferred) — GiGPO state grouping
uid key → anchor state via the `iter_decisions` freeze convention; γ≈0.95;
huge groups expected (4 memory-1 states × whole batch). Separate session.

## 5. Validation plan (supersedes the sketch in multi_turn.md Part 3)
Unit (Phase 2) → invariants + equivalence cross-check + smoke (Phase 3) → SDPO run +
behavioral eval (Phase 4), as detailed above. Nothing long-running before Phase 3
passes.

## 6. Open items to verify at code time
1. Whether `num_repeat` is truly unused on the GRPO advantage path in our fork, and
   whether `_balance_batch`/mini-batch constraints bind with `use_dynamic_bsz` on.
2. How the moral value enters the *training* teacher (assumed baked into
   `reprompt_template`) — confirm before the Phase-4 wrap ablation.
3. Solution injection semantics post-split: a turn-t "solution" sibling comes from a
   *different history*; with `dont_reprompt_on_self_success: true` and feedback-only
   wrapping this may be moot — decide whether to disable solution injection for
   t ≥ 2 or accept it.
4. `_episode_feedback`'s ideal-based text on a `[G_t]` list mis-scales (`ideal` uses
   `n = len(turn_scores) = 1`); harmless while `include_environment_feedback: false`,
   but fix or gate before enabling feedback.
5. Validation-loop path (`test_freq`) runs unsplit — confirm no split-only keys leak
   into it.

## 7. Reference clones
Shallow clones inspected during this investigation (scratchpad, ephemeral):
verl-agent @ 20bd331, RAGEN @ 20daedc, Multi-Turn-RL-Agent @ 7dbdd08,
vanilla verl @ 4a2cba7. Key files if re-cloning: verl-agent
`agent_system/multi_turn_rollout/rollout_loop.py` + `gigpo/core_gigpo.py`; RAGEN
`ragen/llm_agent/ctx_manager.py` + `ragen/trainer/{agent_trainer,core_algos,rollout_filter}.py`.
