# Local patches to `~/SDPO` (vendored verl)

`~/SDPO` is a clone of **`lasgroup/SDPO`**, which vendors **verl `0.7.0.dev`** under `verl/`.
It is installed editable into the training container at job start, so a commit here changes
the next run with no rebuild.

Carrying local patches is a liability: they are invisible to anyone reading upstream, they
silently decide the behaviour of every run, and they rot when the vendored tree moves. This
file is the register. **Every deviation from `origin/main` must appear here.**

Check the register is complete:

```bash
git -C ~/SDPO log --oneline origin/main..HEAD     # must match the table below
```

All local commits carry a `[MORALGYM]` subject prefix so they are greppable.

---

## Register

| commit | date | what | upstream status |
|---|---|---|---|
| `900683b` | 2026-07-06 | log train-time means of numeric reward extras | MoralGym-specific instrumentation — **not** an upstream candidate for verl; possibly for SDPO |
| `4893fd3` | 2026-08-27 | pass `tools=None`, not `[]`, when no tools are configured | **verl bug — PR-worthy.** Not yet filed |
| `76134be` | 2026-08-28 | compute the system-prompt prefix lazily, not in `__init__` | **verl bug — PR-worthy.** Not yet filed |

---

### `4893fd3` — `tools=None`, not `[]`

`verl/experimental/agent_loop/single_turn_agent_loop.py`

`tool_schemas` is `[]` whenever `data.tool_config_path` is unset, and it was forwarded to
`apply_chat_template` unconditionally. Templates guard that argument inconsistently, and an
empty list is not `None`:

```jinja
Qwen3      {%- if tools %}                 [] is falsy      -> skipped
Gemma-2/3  (never references tools)                         -> ignored
Llama-3.1  {%- if tools is not none %}     [] is NOT none   -> RENDERS
```

On Llama-3.1 the empty list rendered the entire tool-calling preamble with **zero functions
listed** — an `Environment: ipython` system block plus *"Given the following functions,
please respond with a JSON for a function call"*. The prompt was silently rewritten and the
model obeyed it, emitting JSON instead of reasoning.

Evidence: `llama31_deon_150` (job 3203600) — 99.3% parse-fail at step 10, and 100% of step-150
rollouts still tool-call shaped. The rerun with this patch (`llama31_deon_150_v2`, job 3204538)
went to 0/256 contaminated prompts and 0.8% parse-fail at step 10.

Scope: no-op when tools are genuinely configured. `ToolAgentLoop` keeps its unconditional
pass because it only runs when tools exist; `rl_dataset.py` already guards with `is not None`.

Full write-up and a ready-to-paste PR body: `handoff_verl_tools_upstream_fix.md`.

### `76134be` — lazy `system_prompt`

`verl/experimental/agent_loop/agent_loop.py`

`AgentLoopBase.__init__` called `initialize_system_prompt()` unconditionally. That function
measures the conversation preamble by subtracting two renders, and its second render is **two
consecutive user messages** — a conversation that cannot occur. Templates that validate the
chat contract reject it: Gemma-2 and Gemma-3 both raise *"Conversation roles must alternate
user/assistant/..."*, killing the job at construction.

The value is consumed at exactly one site — `agent_loop.py:307`, behind
`remove_system_prompt=True`, which only `ToolAgentLoop` passes. **Single-turn never reads it.**
Gemma-3 was dying to compute a number it would discard; that number is one `<BOS>` token.

Evidence: `gemma3_deon_150` jobs 3203587 and 3204247 — failed at 2:04 and 8:29 with zero
checkpoints.

Deferring the probe to first use lets single-turn train on any template, and makes a model
whose template cannot answer the probe fail where the answer is needed, with an error naming
the remedy.

Deliberately **not** fixed here: the probe's arithmetic assumes chat templates are
concatenative, which is false in general (Qwen3). Any differencing scheme inherits that —
see `handoff_qwen3_multiturn_template.md`.

### `900683b` — log train-time means of numeric reward extras

MoralGym-specific instrumentation so `reward_extra_infos_dict` values (cooperation,
reciprocity, exploitation, parse-fail) appear in the training metrics rather than only in
validation. Not a bug fix and not generally useful upstream to verl; it could go to
`lasgroup/SDPO` if the group wants it.

---

## Upstreaming

Two upstreams, and the distinction matters:

- **`volcengine/verl`** — origin of the two template bugs. Both patched files were untouched
  since SDPO's `v1.0.0` import (`git log -- <file>` shows only our commits), so the patches
  apply cleanly there.
- **`lasgroup/SDPO`** — our immediate upstream. Taking the fixes here benefits the group
  without waiting on verl.

Before filing, check whether upstream already fixed either — the vendored tree is
`0.7.0.dev` and may lag. `gh` is not installed on the login node; use the web UI or install it.

## When the vendored tree moves

On any update of `lasgroup/SDPO` or a verl version bump:

1. Rebase the three `[MORALGYM]` commits and confirm the register still matches
   `git log --oneline origin/main..HEAD`.
2. Drop any patch upstream has since fixed, and record that here.
3. Re-run `scripts/preflight/check_model_template.py --multi-turn` — it checks the properties
   these patches exist to protect, so a silently reverted fix shows up as a failing check.
4. Smoke a 2-step debug run before any full run.
