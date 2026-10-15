"""The batched (vLLM) single-round path must reproduce the sequential path
exactly when both see the same answers."""
import copy

import pytest

from moralgym_verl.eval import behavioral
from moralgym_verl.eval.config import apply_protocol, load_config

CONFIG = "configs/eval/harness/qwen3_8b/pgg.yaml"


class _Tok:
    def apply_chat_template(self, messages, tokenize, add_generation_prompt, **kw):
        return "|".join(m["content"] for m in messages)


def _answer(text):
    # deterministic, prompt-dependent answer covering both actions
    return "Action: action3" if hash(text) % 2 else "Action: action4"


def _cfg():
    cfg = load_config(CONFIG)
    apply_protocol(cfg, "single_round")
    cfg["evaluation"]["num_episodes"] = 16
    return cfg


def test_batched_matches_sequential(monkeypatch):
    seq_cfg = _cfg()
    monkeypatch.setattr(behavioral, "build_policy",
                        lambda cfg, ckpt, raw_log=None: (lambda p: _answer(p)))
    sequential = behavioral.evaluate(seq_cfg, None)

    bat_cfg = copy.deepcopy(_cfg())
    bat_cfg["evaluation"]["backend"] = "vllm"
    monkeypatch.setattr(behavioral, "load_vllm_for_eval",
                        lambda *a, **k: (None, _Tok(), None))
    monkeypatch.setattr(behavioral, "generate_vllm",
                        lambda llm, tok, texts, *a, **k: [_answer(t) for t in texts])
    batched = behavioral.evaluate(bat_cfg, None)

    assert batched == sequential


def test_batched_refuses_multi_round():
    cfg = _cfg()
    cfg["game"]["num_rounds"] = 5
    cfg["evaluation"]["backend"] = "vllm"
    with pytest.raises(ValueError, match="single-round"):
        behavioral.evaluate(cfg, None)
