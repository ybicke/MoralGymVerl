"""Mock-model test for probe_episode (Probe B, --states episode).

Covers the training-parity contract of the episode loop: illegal moves
freeze the game state AND prepend parse_failure_feedback to the next
round's user message — exactly what trajectory.run_episode / the verl
interaction do. The generation/scoring boundary is monkeypatched; the
prompt construction, parsing, and state machine are real.
"""

from types import SimpleNamespace

import torch

import moralgym_verl.eval.probe_reasoning_trace as prt
from moralgym_verl.eval.config import build_eval_config
from moralgym_verl.game.prompts import parse_failure_feedback

CFG = {
    "seed": 42,
    "game": {"type": "prisoners_dilemma",
             "payoffs": {"T": 4, "R": 3, "P": 1, "S": 0},
             "num_rounds": 3},
    "prompt": {"game_design": "nohist", "reasoning": True,
               "minimal_parsing": False, "show_horizon": False},
    "evaluation": {},
}


class TokStub:
    def __call__(self, text, add_special_tokens=False, return_tensors="pt"):
        return SimpleNamespace(input_ids=torch.zeros((1, 2), dtype=torch.long))


def test_probe_episode_reprompts_after_parse_failure(monkeypatch):
    config = build_eval_config(CFG, opponent="tit_for_tat")

    # Round 1 illegal, rounds 2-3 legal.
    traces = iter(["complete garbage with no marker",
                   "Reasoning...\n\nAction: action3",
                   "Reasoning...\n\nAction: action3"])
    student_user_msgs = []

    def fake_prefix(tokenizer, messages, device):
        # Student prefixes are plain; teacher prefixes have the FIRST user
        # turn value-wrapped ("[MV]"). Rollout phase runs all student calls
        # before the scoring phase runs any teacher call.
        if not messages[0]["content"].startswith("[MV]"):
            student_user_msgs.append(messages[-1]["content"])
        return torch.zeros((1, 1), dtype=torch.long)

    monkeypatch.setattr(prt, "chat_prefix_messages", fake_prefix)
    monkeypatch.setattr(prt, "sample_trace", lambda *a, **k: next(traces))
    monkeypatch.setattr(
        prt, "dual_continuation_scores",
        lambda *a, **k: {"lp_s": 0.0, "lp_t": 0.0,
                         "num_tokens": 0, "token_jsd": None})
    monkeypatch.setattr(prt, "answer_logodds", lambda *a, **k: (0.0, 0.0, 0.0))

    model = SimpleNamespace(device="cpu")
    records = prt.probe_episode(
        model, TokStub(), config, wrapper=lambda p: f"[MV]\n{p}",
        alpha=0.5, max_new_tokens=8, temperature=0.0,
    )

    assert [r["agent"] for r in records] == ["illegal", "C", "C"]
    assert [r["reprompted"] for r in records] == [False, True, False]

    # Round 2's user message carries the reprompt feedback, training-exact:
    # feedback + blank line + the SAME prompt as round 1 (state frozen).
    feedback = parse_failure_feedback(config)
    assert student_user_msgs[1] == f"{feedback}\n\n{student_user_msgs[0]}"
    # Round 3 is clean again: no feedback, history advanced.
    assert not student_user_msgs[2].startswith(feedback)
    assert student_user_msgs[2] != student_user_msgs[0]
