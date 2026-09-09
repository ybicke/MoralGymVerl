# Handoff: Qwen3 multi-turn chat-template mismatch

**Status 2026-08-28:** single-turn is fine and unblocked for every model. Qwen3 **multi-turn**
is blocked by a template-level mismatch that is a design decision, not a patch. Nothing here
affects any published result — all of those are single-turn.

Pick this up before wiring multi-turn rollouts.

---

## 1. The problem in one paragraph

verl's agent loop builds training sequences by **splicing**: `prompt_ids` for turn *k*, then
the generated `response_ids`, then the next turn's prompt. That assumes

```
render(turn-k prompt) + generated_text   ==   prefix of render(turn-k+1 prompt)
```

Qwen3 violates this. Its generation prompt **prefills** `<think>\n\n</think>\n\n`, and no
history rendering of that same turn reproduces the prefill. So the sequence you train on is
not the sequence the model sees at inference.

## 2. Measured, not assumed

`scripts/preflight/check_model_template.py --multi-turn`

| model | P3 concatenative | **P4 splice-sound** |
|---|---|---|
| Qwen3-8B (`enable_thinking=False`) | NO | **NO** |
| Llama-3.1-8B | yes | **yes** |
| gemma-2-9b (stock) | yes | **yes** |
| gemma-3-12b | yes | **yes** |

P4 is the criterion that matters; P3 is kept only as a diagnostic that localises the cause.

## 3. Where it comes from

Two template sites disagree, deliberately.

**(a) History branch** — an assistant turn renders differently depending on position:

```jinja
{%- if loop.last or (not loop.last and reasoning_content) %}
    '<|im_start|>assistant\n<think>\n' + reasoning_content + '\n</think>\n\n' + content
{%- else %}
    '<|im_start|>assistant\n' + content
```

```
render([u, a])      ->  assistant\n<think>\n\n</think>\n\na1     a1 is last
render([u, a, u])   ->  assistant\na1                            same a1, now history
```

**(b) Generation prompt** — with `enable_thinking=False` the template appends an already
closed, empty think block:

```
<|im_start|>assistant\n<think>\n\n</think>\n\n
```

The model generates *after* `</think>`, so it never opens a thinking block. **This is a
prefill, not a suppression — the model still reasons, in plain text.** That is why the runs
show prose CoT before `Action:`, and why the reasoning is distilled normally.

The mismatch: the generated turn carries the prefilled wrapper, and history rendering (either
branch) does not.

## 4. Fixes already ruled out — do not retry these

| attempt | result |
|---|---|
| force history branch to no-think (`{%- if false %}`) | P3 passes, **P4 still fails** |
| force history branch to think (`{%- if true %}`) | **P4 still fails** |
| `[u], [u,a], [u,a,u]` probe (the old smoke8 note) | wrong: returns 19-char prefix for Qwen3, correct answer is 0 |
| `[u, assistant]` probe | wrong: measures an assistant block, not a user block |

Editing **one** branch cannot work: the mismatch is *between* the generation prompt and the
history rendering, and the two are supposed to differ.

## 5. Real options

1. **Re-render the whole conversation each turn**, then recompute assistant spans for loss
   masking rather than assuming the response is a suffix. Correct by construction and matches
   inference. Structural change to the agent loop. Check whether Qwen3's template carries
   `{% generation %}` markers, which would let `return_assistant_tokens_mask=True` give the
   spans directly.
2. **A Qwen3 template whose generation prompt and history agree** — drop the prefill *and*
   fix one history branch, then re-run P4. Self-consistent, but no longer Qwen3's intended
   format, and it keeps reasoning in context that stock Qwen3 drops. A modelling decision.
3. **Run multi-turn on Llama-3.1 or Gemma**, which already pass P4, and keep Qwen3
   single-turn. Zero code, but splits the model axis across protocols.

Option 1 is the only one that is correct without a caveat.

## 6. What is already done

- `~/SDPO 4893fd3` — `tools=None` instead of `[]` (Llama tool-preamble bug).
- `~/SDPO 76134be` — `system_prompt` computed lazily, so single-turn no longer needs verl's
  two-consecutive-user-messages probe. **This is what unblocked Gemma-3.**
- `scripts/preflight/check_model_template.py` — P1 prompt fidelity, P2 probe, P3
  concatenativity, P4 splice soundness. Pure Jinja, login node, seconds.
- Both SDPO commits are upstream-PR-worthy; see `handoff_verl_tools_upstream_fix.md`.

## 7. Open, in priority order

- [ ] Smoke `gemma3_12b_grpo_pd_deon_tft` on `debug` (2 steps) — never yet reached step 1.
- [ ] Decide the Qwen3 multi-turn option above before building multi-turn rollouts.
- [ ] Add the preflight to `train_verl.sh` as a gate on first submission of a new model.
- [ ] Verify Mistral before claiming it in the upstream issue (same `is not none` idiom).
