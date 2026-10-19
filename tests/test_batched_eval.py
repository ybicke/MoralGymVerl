"""The lockstep (vLLM) driver must reproduce the sequential (hf) driver
exactly when both see the same answers: same episodes, metrics and raw
records, for single-round and multi-round protocols, legal and illegal
moves, deterministic and stochastic opponents."""
import copy

import pytest

from moralgym_verl.eval import behavioral
from moralgym_verl.eval.config import apply_protocol, load_config

CONFIG = "configs/eval/harness/qwen3_8b/pgg.yaml"


class _Tok:
    def apply_chat_template(self, messages, tokenize, add_generation_prompt, **kw):
        return "|".join(f"{m['role']}:{m['content']}" for m in messages)


def _answer(text):
    # Deterministic, context-dependent answer covering both actions and,
    # every fifth call, a parse failure (illegal round).
    h = hash(text)
    if h % 5 == 0:
        return "I pass."
    return "Action: action3" if h % 2 else "Action: action4"


def _cfg(protocol, num_episodes):
    cfg = load_config(CONFIG)
    apply_protocol(cfg, protocol)
    cfg["evaluation"]["num_episodes"] = num_episodes
    return cfg


def _sequential(cfg, monkeypatch, raw_log):
    """hf driver with the HF generate path replaced by _answer; the chat
    policy renders through the same stub tokenizer as the batched one."""
    cfg = copy.deepcopy(cfg)
    cfg["evaluation"]["backend"] = "hf"
    monkeypatch.setattr(behavioral, "load_model_for_eval",
                        lambda ckpt, base: (_Model(), _Tok()))
    import moralgym_verl.eval.generation as generation
    # the stub hands generate() the rendered text as its `inputs`
    monkeypatch.setattr(generation, "render_chat_inputs",
                        lambda tok, messages, device, enable_thinking=None:
                        (tok.apply_chat_template(messages, False, True),) * 2)
    monkeypatch.setattr(generation, "generate",
                        lambda model, tok, inputs, *a: _answer(inputs))
    return behavioral.evaluate(cfg, None, raw_log)


class _Model:
    device = "cpu"


def _batched(cfg, monkeypatch, raw_log):
    cfg = copy.deepcopy(cfg)
    cfg["evaluation"]["backend"] = "vllm"
    monkeypatch.setattr(behavioral, "load_vllm_for_eval",
                        lambda *a, **k: (None, _Tok(), None))
    monkeypatch.setattr(behavioral, "generate_vllm",
                        lambda llm, tok, texts, *a, **k: [_answer(t) for t in texts])
    return behavioral.evaluate(cfg, None, raw_log)


@pytest.mark.parametrize("protocol,num_episodes", [
    ("single_round", 16),   # opponent: random (stochastic bot)
    ("multi_round", 8),     # full_contributor, free_rider, noisy_conditional
])
def test_batched_matches_sequential(monkeypatch, protocol, num_episodes):
    cfg = _cfg(protocol, num_episodes)
    seq_log, bat_log = [], []
    sequential = _sequential(cfg, monkeypatch, seq_log)
    batched = _batched(cfg, monkeypatch, bat_log)
    assert batched == sequential
    assert bat_log == seq_log
    rounds = cfg["game"]["num_rounds"]
    opponents = len(cfg["evaluation"]["opponents"])
    assert len(bat_log) == rounds * num_episodes * opponents
    if rounds > 1:
        # multi-round records hold the rendered context, which grows
        assert "assistant:" in bat_log[1]["prompt"]
        assert "assistant:" not in bat_log[0]["prompt"]
    else:
        # single-round records hold the game prompt, not the template
        assert not bat_log[0]["prompt"].startswith("user:")


def test_multi_round_batches_once_per_round(monkeypatch):
    cfg = _cfg("multi_round", 4)
    calls = []

    def fake_generate(llm, tok, texts, *a, **k):
        calls.append(len(texts))
        return [_answer(t) for t in texts]

    cfg["evaluation"]["backend"] = "vllm"
    monkeypatch.setattr(behavioral, "load_vllm_for_eval",
                        lambda *a, **k: (None, _Tok(), None))
    monkeypatch.setattr(behavioral, "generate_vllm", fake_generate)
    behavioral.evaluate(cfg, None)
    n = 4 * len(cfg["evaluation"]["opponents"])
    assert calls == [n] * cfg["game"]["num_rounds"]


def test_default_backend_is_vllm():
    assert behavioral.resolve_backend(_cfg("single_round", 4)) == "vllm"
    cfg = _cfg("single_round", 4)
    cfg["evaluation"]["backend"] = "hf"
    assert behavioral.resolve_backend(cfg) == "hf"
