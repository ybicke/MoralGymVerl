"""Prompt rendering, sampling, and policy functions for the behavioral eval.

Everything that touches the model at generation time lives here; the
experiment entry points (behavioral, probes) compose these into policies.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import torch

from moralgym_verl.eval.teacher_context import wrap_first_user, wrap_latest_user


def render_chat_text(tokenizer, messages, enable_thinking=None):
    """Chat-template `messages` to the generation prompt string."""
    extra = {} if enable_thinking is None else {"enable_thinking": enable_thinking}
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, **extra,
    )


def render_chat_inputs(tokenizer, messages, device, enable_thinking=None):
    """Chat-template `messages` into generation inputs, training-exact.

    add_special_tokens=False: the rendered template already starts with
    <bos>; the HF default would prepend a second, deviating from verl's
    training tokenization. Returns (rendered_text, model inputs).

    enable_thinking is passed through only when set, so templates that
    don't branch on it render exactly as before.
    """
    text = render_chat_text(tokenizer, messages, enable_thinking)
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


class Conversation:
    """One episode's chat transcript and its generation-time rendering.

    The stored transcript stays plain; with `prompt_wrapper` (teacher
    context) `wrap_position` picks where the value appears at generation
    time: 'first' = the episode's first user turn, training-exact for
    multi-turn SDPO (see teacher_context.wrap_first_user); 'latest' = the
    current turn (persistent-context ablation). A single-round episode is
    a one-turn conversation, so the same object serves both protocols.
    """

    def __init__(self, tokenizer, prompt_wrapper=None,
                 wrap_position: str = "first",
                 enable_thinking: Optional[bool] = None):
        if wrap_position not in ("first", "latest"):
            raise ValueError(f"wrap_position must be 'first' or 'latest', "
                             f"got {wrap_position!r}")
        self.tokenizer = tokenizer
        self.prompt_wrapper = prompt_wrapper
        self.wrap_fn = (wrap_first_user if wrap_position == "first"
                        else wrap_latest_user)
        self.enable_thinking = enable_thinking
        self.messages: List[Dict[str, str]] = []

    def reset(self) -> None:
        self.messages.clear()

    def push(self, prompt: str) -> List[Dict[str, str]]:
        """Append the user turn; return the messages to generate from."""
        self.messages.append({"role": "user", "content": prompt})
        return (self.wrap_fn(self.messages, self.prompt_wrapper)
                if self.prompt_wrapper is not None else self.messages)

    def render(self, prompt: str) -> str:
        """Append the user turn and return the generation prompt text."""
        return render_chat_text(self.tokenizer, self.push(prompt),
                                self.enable_thinking)

    def record(self, raw: str) -> None:
        """Append the model's turn."""
        self.messages.append({"role": "assistant", "content": raw})

    def log_prompt(self, rendered: str) -> str:
        """What raw_log stores for this call: the wrapped prompt for a
        one-turn conversation (offline analysis reads it as the game
        prompt), the fully rendered context once the transcript has
        history, so the text the model actually saw is inspectable."""
        user_turns = [m for m in self.messages if m["role"] == "user"]
        if len(user_turns) > 1:
            return rendered
        prompt = user_turns[-1]["content"]
        return (self.prompt_wrapper(prompt) if self.prompt_wrapper is not None
                else prompt)


def _hf_policy_fn(model, tokenizer, conv: Conversation, max_new_tokens,
                  temperature, raw_log):
    def policy_fn(prompt: str) -> str:
        text, inputs = render_chat_inputs(tokenizer, conv.push(prompt),
                                          model.device, conv.enable_thinking)
        raw = generate(model, tokenizer, inputs, max_new_tokens, temperature)
        conv.record(raw)
        if raw_log is not None:
            raw_log.append({"prompt": conv.log_prompt(text), "raw": raw})
        return raw

    return policy_fn


def make_policy_fn(
    model,
    tokenizer,
    max_new_tokens: int = 10,
    temperature: Optional[float] = 1.0,
    raw_log: Optional[list] = None,
    prompt_wrapper=None,
    enable_thinking: Optional[bool] = None,
):
    """Stateless single-round policy for run_episode: every call is a
    fresh one-turn conversation.

    Default T=1.0 multinomial (top_k=0, top_p=1.0) mirrors Tennant's
    trl respond_to_batch — measures the stochastic policy as trained;
    temperature=0/None -> greedy. raw_log (list) collects {"prompt",
    "raw"} per call, with the prompt as wrapped, so raw_log records
    exactly what the model saw.
    """
    conv = Conversation(tokenizer, prompt_wrapper, "first", enable_thinking)
    inner = _hf_policy_fn(model, tokenizer, conv, max_new_tokens,
                          temperature, raw_log)

    def policy_fn(prompt: str) -> str:
        conv.reset()
        return inner(prompt)

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
    """Transcript-mode policy for multi-round eval: the episode
    conversation accumulates (see Conversation), mirroring verl's
    multi-turn agent loop. The caller MUST call policy_fn.reset() between
    episodes (run_opponent does) — otherwise conversations leak across
    episodes.
    """
    conv = Conversation(tokenizer, prompt_wrapper, wrap_position,
                        enable_thinking)
    policy_fn = _hf_policy_fn(model, tokenizer, conv, max_new_tokens,
                              temperature, raw_log)
    policy_fn.reset = conv.reset
    return policy_fn


# --- vLLM path --------------------------------------------------------------


def generate_vllm(llm, tokenizer, texts: List[str], max_new_tokens: int,
                  temperature: Optional[float], lora_request=None) -> List[str]:
    """Sample one response per rendered prompt in a single vLLM call.

    Same decoding as generate(): full distribution at T>0 (no top-k/top-p),
    greedy at T<=0; tokenized without special tokens, as in training. The
    sampling seed is the engine's, so repeated prompts draw independently.
    """
    from vllm import SamplingParams
    from vllm.inputs import TokensPrompt

    params = SamplingParams(
        temperature=temperature if temperature and temperature > 0 else 0.0,
        top_p=1.0, top_k=0, max_tokens=max_new_tokens,
    )
    prompts = [TokensPrompt(prompt_token_ids=tokenizer(
        t, add_special_tokens=False)["input_ids"]) for t in texts]
    outputs = llm.generate(prompts, params, lora_request=lora_request,
                           use_tqdm=False)
    return [o.outputs[0].text for o in outputs]
