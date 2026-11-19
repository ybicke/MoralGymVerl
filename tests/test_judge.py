"""Tests for the LLM-judge reward (training/judge.py). Torch-free: run on the
login node / locally. The HTTP tests use a fake vLLM server on localhost.

Design: ~/moralgym-report/notes/judge_grpo_design.tex.
"""

import json
import math
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.training import judge as J
from moralgym_verl.training.dataset import make_sample
from moralgym_verl.training.reward_fn import compute_score

DATASET = "configs/datasets/grpo_pd_judge-principle_tft.yaml"
JUDGE_BLOCK = {"model": "Qwen/Qwen3-8B", "moral_value": "deontological"}


def _sample(seed=1):
    random.seed(seed)
    cfg = yaml.safe_load(open(DATASET))
    s = make_sample(cfg)
    return s, json.loads(s["reward_model"]["ground_truth"])


def _top(**probs):
    return [{"token": t, "logprob": math.log(p)} for t, p in probs.items()]


# ── What the judge reads ─────────────────────────────────────────────────────

def test_rebuilt_game_prompt_is_the_policy_prompt():
    for seed in range(20):
        s, state = _sample(seed)
        assert J.game_prompt_from_state(state) == s["prompt"][0]["content"]


def test_judge_prompt_mirrors_sdpo_teacher_order():
    s, state = _sample()
    p = J.build_judge_prompt(state, "my reasoning. Action: x", "deontological")
    game, principle = s["prompt"][0]["content"], get_moral_value("deontological")
    # Game, then the principle in the SDPO feedback wording, then the
    # response, then the question.
    i_game = p.index(game)
    i_val = p.index("Moral value to follow:\n" + principle)
    i_resp = p.index("my reasoning. Action: x")
    i_q = p.index(J.QUESTIONS["yesno"])
    assert i_game < i_val < i_resp < i_q


def test_judge_never_sees_the_opponent_strategy_or_outcome():
    # The opponent's next move depends on its type; the judge prompt must not.
    _, state = _sample()
    a = J.build_judge_prompt(state, "r", "deontological")
    state["opponent"] = "always_defect"
    assert J.build_judge_prompt(state, "r", "deontological") == a


def test_scale_mode_asks_for_a_digit():
    _, state = _sample()
    p = J.build_judge_prompt(state, "r", "deontological", "scale")
    assert p.endswith(J.QUESTIONS["scale"]) and "0 (not at all) to 9 (fully)" in p


# ── How the score is read ────────────────────────────────────────────────────

def test_p_yes_renormalizes_over_yes_and_no_variants():
    top = _top(**{"Yes": 0.6, " yes": 0.1, "No": 0.2, "The": 0.1})
    assert J.p_yes(top) == pytest.approx(0.7 / 0.9)


def test_p_yes_none_without_an_answer():
    assert J.p_yes(_top(The=0.9, I=0.1)) is None


def test_expected_digit_is_the_mean_digit_over_9():
    top = _top(**{"9": 0.5, "3": 0.25, " 6": 0.25, "x": 0.1})  # " 6" counts as 6
    assert J.expected_digit(top) == pytest.approx((0.5 * 9 + 0.25 * 3 + 0.25 * 6) / 9)
    assert J.expected_digit(_top(x=1.0)) is None


def test_answer_position_with_and_without_thinking():
    tok = lambda t: {"token": t, "logprob": 0.0, "top_logprobs": []}
    assert J.answer_position([tok("Yes")], thinking=False) == 0
    content = [tok("<think>"), tok("hmm"), tok("</think>"), tok("\n\n"), tok("No")]
    assert J.answer_position(content, thinking=True) == 4
    # Never closed its reasoning (cap hit): no answer.
    assert J.answer_position(content[:2], thinking=True) is None


# ── Config ───────────────────────────────────────────────────────────────────

def test_config_rejects_typos_and_unknown_values():
    with pytest.raises(ValueError, match="Unknown judge config keys"):
        J.JudgeConfig.from_dict({**JUDGE_BLOCK, "thinkng": True})
    with pytest.raises(ValueError, match="scoring"):
        J.JudgeConfig.from_dict({**JUDGE_BLOCK, "scoring": "likert"})
    with pytest.raises(ValueError):
        J.JudgeConfig.from_dict({**JUDGE_BLOCK, "moral_value": "nonexistent"})


def test_endpoint_from_env(monkeypatch):
    monkeypatch.setenv("MORALGYM_JUDGE_URL", "http://node1:8000/v1/")
    assert J.JudgeConfig.from_dict(JUDGE_BLOCK).endpoint == "http://node1:8000/v1"


def test_dataset_carries_and_validates_the_judge_block():
    _, state = _sample()
    assert state["judge"]["model"] == "Qwen/Qwen3-8B"
    cfg = yaml.safe_load(open(DATASET))
    cfg["judge"]["scoring"] = "likert"
    with pytest.raises(ValueError):
        make_sample(cfg)


# ── From judge score to reward ───────────────────────────────────────────────

def test_apply_judge_rewards():
    s, state = _sample()
    gt = s["reward_model"]["ground_truth"]
    labels = (state["coop_label"], state["defect_label"])
    responses = [f"I keep trust. Action: {labels[0]}",
                 f"More points. Action: {labels[1]}",
                 "no action given"]
    results = [compute_score("moralgym", r, gt) for r in responses]
    asked = []

    def fake(cfg, states, resps):
        asked.extend(resps)
        return [0.9, None]

    out = J.apply_judge(results, [state] * 3, responses, client=fake)
    # The unparseable response is never sent to the judge.
    assert asked == responses[:2]
    # Judge score is the reward; None -> neutral 0.5; parse failure -> -6.
    assert [o["score"] for o in out] == [0.9, J.INVALID_SCORE, -6.0]
    assert [o["judge_invalid_rate"] for o in out] == [0.0, 1.0, 0.0]
    # Game payoff kept for logging; SDPO feedback (the principle) untouched.
    assert [o["game_score"] for o in out] == [r["score"] for r in results]
    assert all(o["feedback"] == r["feedback"] for o, r in zip(out, results))
    # Same keys for every sample (verl batch-means them).
    assert len({tuple(sorted(o)) for o in out}) == 1


def test_judge_approving_exploitation_is_flagged():
    s, state = _sample()
    state["opp_history"] = ["C"]  # the other player cooperated last round
    gt = json.dumps(state)
    resp = f"Action: {state['defect_label']}"
    res = [compute_score("moralgym", resp, gt)]
    out = J.apply_judge(res, [state], [resp], client=lambda c, s, r: [0.8])
    assert out[0]["judge_approves_exploitation_rate"] == 1.0


# ── HTTP, against a fake vLLM server ─────────────────────────────────────────

class _FakeVLLM(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeVLLM.bodies.append(body)
        first = {"token": "Yes", "logprob": math.log(0.75),
                 "top_logprobs": _top(Yes=0.75, No=0.25)}
        out = {"choices": [{"logprobs": {"content": [first]}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    _FakeVLLM.bodies = []
    srv = HTTPServer(("127.0.0.1", 0), _FakeVLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_judge_scores_over_http(fake_server):
    _, state = _sample()
    cfg = J.JudgeConfig.from_dict({**JUDGE_BLOCK, "endpoint": fake_server})
    scores = J.judge_scores(cfg, [state] * 5, ["a", "b", "c", "d", "e"])
    assert scores == [pytest.approx(0.75)] * 5
    body = _FakeVLLM.bodies[0]
    assert body["max_tokens"] == 1 and body["temperature"] == 0.0
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_request_samples_with_a_budget(fake_server):
    cfg = J.JudgeConfig.from_dict({**JUDGE_BLOCK, "thinking": True})
    body = J._request_body(cfg, "x")
    assert body["max_tokens"] > cfg.max_think_tokens and body["temperature"] == 0.6
    assert body["chat_template_kwargs"] == {"enable_thinking": True}


def test_judge_down_raises_instead_of_scoring(monkeypatch):
    monkeypatch.setattr(J.time, "sleep", lambda s: None)
    _, state = _sample()
    # Port 9 (discard) on localhost: connection refused.
    cfg = J.JudgeConfig.from_dict({**JUDGE_BLOCK, "endpoint": "http://127.0.0.1:9/v1", "retries": 2})
    with pytest.raises(J.JudgeUnavailable):
        J.judge_scores(cfg, [state], ["r"])


def test_no_endpoint_raises(monkeypatch):
    monkeypatch.delenv("MORALGYM_JUDGE_URL", raising=False)
    _, state = _sample()
    with pytest.raises(J.JudgeUnavailable, match="no judge endpoint"):
        J.judge_scores(J.JudgeConfig.from_dict(JUDGE_BLOCK), [state], ["r"])
