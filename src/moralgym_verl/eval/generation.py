"""Prompt rendering, sampling, and policy functions for the behavioral eval.

Everything that touches the model at generation time lives here; the
experiment entry points (behavioral, probes) compose these into policies.
"""

from __future__ import annotations

from typing import Dict, Optional

import torch

from moralgym_verl.eval.teacher_context import wrap_first_user, wrap_latest_user


def render_chat_inputs(tokenizer, messages, device, enable_thinking=None):
    """Chat-template `messages` into generation inputs, training-exact.

    add_special_tokens=False: the rendered template already starts with
    <bos>; the HF default would prepend a second, deviating from verl's
    training tokenization. Returns (rendered_text, model inputs).

    enable_thinking is passed through only when set, so templates that
    don't branch on it render exactly as before.
    """
    extra = {} if enable_thinking is None else {"enable_thinking": enable_thinking}
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, **extra,
    )
    inputs = tokenizer(
        text, return_tensors="pt", add_special_tokens=False,
    ).to(device)
    return text, inputs


@torch.no_grad()
def generate(model, tokenizer, inputs, max_new_tokens, temperature):
    """Sample one response from prepared inputs.

    T>0: multinomial with top_k=0 / top_p=1.0 (overriding HF's top_k=50)
    — full unclipped distribution, matching Tennant's multinomial path.
    T<=0 or None: greedy argmax.
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
    enable_thinking: Optional[bool] = None,
):
    """Policy function for run_episode.

    Default T=1.0 multinomial (top_k=0, top_p=1.0) mirrors Tennant's
    trl respond_to_batch — measures the stochastic policy as trained;
    temperature=0/None -> greedy. raw_log (list) collects {"prompt",
    "raw"} per call. prompt_wrapper (teacher-signal eval) is applied
    before chat templating, so raw_log records exactly what the model saw.
    """

    def policy_fn(prompt: str) -> str:
        if prompt_wrapper is not None:
            prompt = prompt_wrapper(prompt)
        messages = [{"role": "user", "content": prompt}]
        _, inputs = render_chat_inputs(tokenizer, messages, model.device,
                                       enable_thinking)
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
    enable_thinking: Optional[bool] = None,
):
    """Transcript-mode policy for multi-turn eval (Stage 1b): the episode
    conversation accumulates, mirroring verl's multi-turn agent loop, so
    history-dependent behavior is expressible exactly as in training.

    The stored transcript stays plain; with `prompt_wrapper`,
    `wrap_position` picks where the value appears at generation time:
    'first' (default) = episode's first user turn, training-exact for
    multi-turn SDPO (see teacher_context.wrap_first_user); 'latest' =
    current turn (persistent-context ablation). The caller MUST call
    policy_fn.reset() between episodes (evaluate() does) — otherwise
    conversations leak across episodes.
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
        text, inputs = render_chat_inputs(tokenizer, gen_messages, model.device,
                                          enable_thinking)
        raw = generate(model, tokenizer, inputs, max_new_tokens, temperature)
        state["messages"].append({"role": "assistant", "content": raw})
        if raw_log is not None:
            # Log the fully rendered context, not just the last message —
            # the transcript the model actually saw is then inspectable.
            raw_log.append({"prompt": text, "raw": raw})
        return raw

    policy_fn.reset = lambda: state["messages"].clear()
    return policy_fn
