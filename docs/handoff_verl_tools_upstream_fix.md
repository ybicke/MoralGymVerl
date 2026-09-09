# Handoff: upstreaming the `tools=[]` chat-template fix

**Status 2026-08-27:** fixed locally, committed, verified. Not yet reported upstream.
**Local commit:** `4893fd3` on `main` in `~/SDPO` (`lasgroup/SDPO`).
**Next action:** file an issue + PR against `volcengine/verl`. Everything you need is below.

---

## 1. The bug in one paragraph

`SingleTurnAgentLoop` sets `tool_schemas = []` whenever `data.tool_config_path` is unset,
then forwards it to `apply_chat_template` unconditionally as `tools=[]`. Chat templates
disagree on how to guard that argument, and an empty list is *not* `None`. On Llama-3.1
(and any template using the `is not none` idiom) this renders the model's full tool-calling
preamble with **zero functions listed** — silently rewriting the prompt into a
function-calling instruction that the user never asked for.

## 2. Exact location (upstream line numbers, before our patch)

`verl/experimental/agent_loop/single_turn_agent_loop.py`

```python
# lines 37-38 — __init__
tool_list = initialize_tools_from_config(tool_config_path) if tool_config_path else []
self.tool_schemas = [tool.tool_schema.model_dump(...) for tool in tool_list]   # -> []

# line 51 — run()
prompt_ids = await self.apply_chat_template(
    messages,
    tools=self.tool_schemas,        # <-- passes [] unconditionally
    images=images,
    videos=videos,
)
```

`apply_chat_template` (`agent_loop.py:246`) forwards `tools` straight through to
`tokenizer.apply_chat_template` / `processor.apply_chat_template`.

## 3. Why it only breaks some models

| model | template guard | result with `tools=[]` |
|---|---|---|
| Qwen3 | `{%- if tools %}` | falsy → skipped ✓ |
| Gemma-3 | template never references `tools` | ignored ✓ |
| **Llama-3.1** | `{%- if tools is not none %}` | `[]` is not `none` → **renders** ✗ |

Mistral's template uses the same `is not none` idiom, so it is very likely affected too
(not verified — worth checking before claiming it in the issue).

## 4. What the broken prompt looks like

Rendered for Llama-3.1-8B-Instruct with `tools=[]`:

```
<|begin_of_text|><|start_header_id|>system<|end_header_id|>

Environment: ipython
Cutting Knowledge Date: December 2023
Today Date: 26 Jul 2024
<|eot_id|><|start_header_id|>user<|end_header_id|>

Given the following functions, please respond with a JSON for a function call with its
proper arguments that best answers the given prompt.

Respond in the format {"name": function name, "parameters": ...}.Do not use variables.

You are playing a game with agent A. ...
```

Note "the following functions" is followed by **no functions** — the tell-tale sign.

With `tools=None` both the `Environment: ipython` block and the entire function-call
instruction disappear, leaving just the user prompt.

## 5. Evidence from our runs

Run `llama31_deon_150`, SLURM job 3203600, Llama-3.1-8B-Instruct, GRPO, 150 steps
(wandb `2gglzqxc`):

| step | parse-fail | note |
|---|---|---|
| 10 | 99.3% | bare JSON, no `Action:` line |
| 20 | 57.5% | |
| 30 | ~0.1% | policy learned to append `Action: X` *after* the JSON |
| 110-140 | 0.5-0.7% | |
| 150 | 3.9% | incl. a 5918-char runaway repeating one JSON call |

At step 150, **256/256 rollouts are still tool-call shaped**, mean 869 chars:

```
{"name": "choose_action", "parameters": {"agent": "A", "last_round": "[action1, action1]",
 "last_points": [3, 3], "action1_points": [3, 0], "action2_points": [0, 4]}}

Action: action1
```

The parse-fail rate recovering to ~0 hides the problem: the reasoning channel is empty,
the model just learned to satisfy the answer parser. The twin Qwen3 run
(`grpo_util_tft_150`, job 3203397) is 0% tool-call shaped with ~1759 chars of prose.

**This is not the historical Llama `\n\n` parse-fail bug** (NeMo-RL, Llama-3.0, 2026-05,
single-token generations at a flat 100%). Different mode, different cause, and this one is
a verl bug rather than a model defect.

## 6. The fix

```diff
--- a/verl/experimental/agent_loop/single_turn_agent_loop.py
+++ b/verl/experimental/agent_loop/single_turn_agent_loop.py
@@ -48,7 +48,7 @@
         prompt_ids = await self.apply_chat_template(
             messages,
-            tools=self.tool_schemas,
+            tools=self.tool_schemas or None,
             images=images,
             videos=videos,
         )
```

No-op when tools are genuinely configured. Alternative framing if a reviewer prefers it:
set `self.tool_schemas = tool_list_schemas or None` in `__init__` instead.

**Do not change `tool_agent_loop.py:215`** — it has the same unconditional pass, but
`ToolAgentLoop` only runs when tools *are* configured, so its list is non-empty.
`rl_dataset.py` already guards correctly with `if self.tool_schemas is not None`.

## 7. Standalone reproduction (no cluster needed)

Runs on the login node with `/usr/bin/python3.11`. Paste into the issue.

```python
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("meta-llama/Llama-3.1-8B-Instruct")
msgs = [{"role": "user", "content": "Say hello."}]

broken = tok.apply_chat_template(msgs, tools=[],   add_generation_prompt=True, tokenize=False)
fixed  = tok.apply_chat_template(msgs, tools=None, add_generation_prompt=True, tokenize=False)

print("tools=[]   -> tool preamble:", "function call" in broken)   # True   <-- bug
print("tools=None -> tool preamble:", "function call" in fixed)    # False
```

Our jinja-level version (avoids the gated download) is in the session log; the
`tools=[]` vs `tools=None` render was diffed directly against the cached snapshot at
`$HF_HOME/hub/models--meta-llama--Llama-3.1-8B-Instruct/snapshots/0e9e39f...`.

## 8. How to submit upstream

The bug is **verl's**, not SDPO's. `~/SDPO` vendors verl `0.7.0.dev`, and
`git log -- verl/experimental/agent_loop/single_turn_agent_loop.py` shows the file was
untouched since SDPO's initial `v1.0.0` import — so the patch applies cleanly to
`volcengine/verl` upstream.

**Primary target:** https://github.com/volcengine/verl
**Secondary (optional):** https://github.com/lasgroup/SDPO — so the group's fork gets it
without waiting on upstream. Our `4893fd3` is already the commit for that.

`gh` is **not installed** on the login node. Either use the GitHub web UI, or
`pip install --user gh` / download the binary if you prefer CLI.

### Steps

1. Check it isn't already reported/fixed:
   - search verl issues for `tool_schemas`, `apply_chat_template tools`, `Environment: ipython`
   - check current `main`: does `single_turn_agent_loop.py` still pass `tools=self.tool_schemas`?
     If upstream already guards it, we only need to rebase our vendored copy.
2. Open an issue using sections 1-5 + 7 below as the body.
3. Fork, branch, apply the one-line change, PR referencing the issue:
   ```bash
   git clone https://github.com/volcengine/verl && cd verl
   git checkout -b fix/single-turn-agent-loop-empty-tools
   # apply the diff from section 6
   git commit -am "fix: pass tools=None instead of [] when no tools are configured"
   ```
4. verl requires DCO sign-off on commits — use `git commit -s`. Check
   `CONTRIBUTING.md` in the repo for the current requirements (tests, pre-commit, lint).

### Draft PR title

> fix(agent_loop): pass `tools=None` instead of `[]` when no tools are configured

### Draft PR body

> **Problem.** `SingleTurnAgentLoop` sets `tool_schemas = []` when `data.tool_config_path`
> is unset and forwards it unconditionally as `tools=[]` to `apply_chat_template`. Chat
> templates guard this argument inconsistently: Qwen3 uses `{%- if tools %}` (an empty
> list is falsy, so it is skipped), but Llama-3.1 uses `{%- if tools is not none %}` — and
> `[]` is not `None`. On Llama-3.1 the empty list therefore renders the model's entire
> tool-calling preamble with zero functions listed: an `Environment: ipython` system block
> plus "Given the following functions, please respond with a JSON for a function call...".
>
> The user's prompt is silently rewritten into a function-calling instruction, and the
> model complies — emitting tool-call JSON instead of the requested reasoning. In a
> 150-step GRPO run on Llama-3.1-8B-Instruct we saw 99.3% parse failures at step 10, and
> 100% of rollouts still tool-call shaped at step 150 (the policy had learned to append a
> parseable answer line *after* the JSON, which masks the problem in the metrics).
>
> Repro: [paste section 7]
>
> **Fix.** Pass `None` when the list is empty. No-op when tools are genuinely configured.
>
> `ToolAgentLoop` intentionally keeps the unconditional pass — it only runs when tools are
> configured. `rl_dataset.py` already guards with `is not None`; this aligns the agent-loop
> path with it.

## 9. Local follow-up (independent of upstream)

- [ ] **Rerun `llama31_deon_150` with the fix.** The old run is not comparable to the Qwen
      arms — different system message, extra instruction. Watch that `parse_fail_rate`
      starts near 0, not 0.99, and that the prompt has no `Environment: ipython` line.
- [ ] HF repo `moralgym/llama-3.1-8b-instruct-pd-grpo-deon-tft-step150` was **deleted**
      2026-08-27 for this reason. Checkpoints remain on `$STORE` under `llama31_deon_150`.
- [ ] `gemma3_deon_150` (job 3204247) is **unaffected** — Gemma-3's template never
      references `tools` — but it picks up the patched code anyway.
- [ ] Verify Mistral's template before claiming it in the issue.
