"""Prompt rendering, sampling, and policy functions for the behavioral eval.

Everything that touches the model at generation time lives here; the
experiment entry points (behavioral, probes) compose these into policies.
"""

from __future__ import annotations

from typing import Dict, Optional

import torch

from moralgym_verl.eval.teacher_context import wrap_first_user, wrap_latest_user


def render_chat_inputs(tokenizer, messages, device):
    """Chat-template `messages` into generation inputs, training-exact.

    add_special_tokens=False because the rendered template already starts
    with <bos> — the HF default would prepend a second one, deviating from
    verl's training tokenization. Returns (rendered_text, model inputs).
    """
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    inputs = tokenizer(
        text, return_tensors="pt", add_special_tokens=False,
    ).to(device)
    return text, inputs


@torch.no_grad()
def generate(model, tokenizer, inputs, max_new_tokens, temperature):
    """Sample one response from prepared inputs.

    T>0: multinomial sampling with top_k=0 / top_p=1.0 — explicitly
    overriding HF's defaults (top_k=50) so sampling is from the full
    unclipped distribution, matching Tennant's
    torch.multinomial(F.softmax(logits)) path. T<=0 or None: greedy argmax.
    """
    use_sampling = temperature is not None and temperature > 0
    gen_kwargs = {"max_new_tokens": max_new_tokens, "do_sample": use_sampling}
    if use_sampling:
        gen_kwargs["temperature"] = temperature
        gen_kwargs["top_k"] = 0
        gen_kwargs["top_p"] = 1.0
    outputs = model.generate(**inputs, **gen_kwargs)
    generated = outputs[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True)


def make_policy_fn(
    model,
    tokenizer,
    max_new_tokens: int = 10,
    temperature: Optional[float] = 1.0,
    raw_log: Optional[list] = None,
    prompt_wrapper=None,
):
    """Policy function for run_episode.

    Default: T=1.0 multinomial sampling with top_k=0, top_p=1.0 — mirrors
    Tennant's `respond_to_batch(..., top_k=0, top_p=1.0)` from trl.core
    (used in every generation call in her inference_vsRandom.py). This
    measures the stochastic policy the model was trained to emit, and
    matches GovSim-style deployment where greedy NL decoding can degenerate.

    Pass `temperature=0` (or None) to fall back to greedy argmax.

    If `raw_log` is a list, each call appends a dict
    {"prompt": <user_text>, "raw": <model_output>} for offline inspection
    (e.g. debugging unexpected parse failures on hyper-peaked policies).

    `prompt_wrapper` (teacher-signal eval) is applied to the game prompt
    before chat templating — raw_log therefore records the wrapped
    prompt, i.e. exactly what the model saw.
    """

    def policy_fn(prompt: str) -> str:
        if prompt_wrapper is not None:
            prompt = prompt_wrapper(prompt)
        messages = [{"role": "user", "content": prompt}]
        _, inputs = render_chat_inputs(tokenizer, messages, model.device)
        raw = generate(model, tokenizer, inputs, max_new_tokens, temperature)
        if raw_log is not None:
            raw_log.append({"prompt": prompt, "raw": raw})
        return raw

    return policy_fn


def make_chat_policy_fn(
    model,
    tokenizer,
    max_new_tokens: int = 10,
    temperature: Optional[float] = 1.0,
    raw_log: Optional[list] = None,
    prompt_wrapper=None,
    wrap_position: str = "first",
):
    """Transcript-mode policy for multi-turn eval (Stage 1b).

    Mirrors verl's multi-turn agent loop (SDPO tool_agent_loop.py): the
    episode conversation accumulates — every prior round's user message
    AND the model's own responses stay in context, so history-dependent
    behavior (grudges, forgiveness) is expressible exactly as in training.

    Teacher semantics: the stored transcript is always plain; when
    `prompt_wrapper` is set, `wrap_position` decides where the moral
    value appears at generation time:
      'first'  (default) — wrap only the episode's FIRST user turn.
                Training-exact: multi-turn SDPO wraps raw_prompt (the
                initial message); all later rounds are shared response-
                region tokens (see teacher_context.wrap_first_user).
      'latest' — wrap the current round's user turn (persistent-context
                ablation; measures the "value always adjacent" variant).

    The caller MUST call `policy_fn.reset()` between episodes (evaluate()
    does) — otherwise conversations leak across episodes.
    """
    if wrap_position not in ("first", "latest"):
        raise ValueError(f"wrap_position must be 'first' or 'latest', "
                         f"got {wrap_position!r}")

    state: Dict[str, list] = {"messages": []}
    wrap_fn = wrap_first_user if wrap_position == "first" else wrap_latest_user

    def policy_fn(prompt: str) -> str:
        state["messages"].append({"role": "user", "content": prompt})
        gen_messages = (
            wrap_fn(state["messages"], prompt_wrapper)
            if prompt_wrapper is not None else state["messages"]
        )
        text, inputs = render_chat_inputs(tokenizer, gen_messages, model.device)
        raw = generate(model, tokenizer, inputs, max_new_tokens, temperature)
        state["messages"].append({"role": "assistant", "content": raw})
        if raw_log is not None:
            # Log the fully rendered context, not just the last message —
            # the transcript the model actually saw is then inspectable.
            raw_log.append({"prompt": text, "raw": raw})
        return raw

    policy_fn.reset = lambda: state["messages"].clear()
    return policy_fn
