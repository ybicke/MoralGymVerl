"""Env-message protocol for multi-round (conversation) episodes.

Since 2026-08-08 multi-round episodes are always conversations: round 1 is
the full prompt, rounds >= 2 carry only build_env_message (outcome +
optional round clause + answer-format line; payoff block re-inserted only
under restate_rules_per_round). Covers the builder content and the
training/eval lockstep: game_interaction.generate_response and
trajectory.run_episode must emit identical messages, including the
illegal-round feedback composition. See
docs/multi_turn_implementation_plan.md Phase 1.
"""

import asyncio
import json

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.prompts import (
    _build_payoff_block, build_env_message, build_prompt, parse_failure_feedback,
)
from moralgym_verl.game.episode import run_episode
from moralgym_verl.training.game_interaction import GameInteraction

RULES_MARKER = "The points are awarded as follows"
HISTORY_MARKER = "You have played with A before"


def make_config(**overrides):
    base = dict(
        game_type="prisoners_dilemma",
        T=4, R=3, P=1, S=0,
        opponent="tit_for_tat",
        num_rounds=3,
        coop_label="action1",
        defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
    )
    base.update(overrides)
    return EpisodeConfig(**base)


# ------------------------------------------------ builder content

def test_minimal_message_content():
    msg = build_env_message(make_config(), "C", "D", round_idx=2)
    # agent C vs opp D -> agent gets S=0, opponent gets T=4
    assert "A chose action2: you got 0 points and A got 4 points." in msg
    assert "choose either action1 or action2" in msg
    assert RULES_MARKER not in msg
    assert HISTORY_MARKER not in msg


def test_question_and_format_restated_every_round():
    """The rules-retention knob is about RULES: the objective and the output
    contract are restated unconditionally (instruction decay, not missing
    facts, is the multi-turn failure mode)."""
    msg = build_env_message(make_config(), "C", "D", round_idx=2)
    assert "What action would you take in order to achieve the highest " \
           "possible score in points?" in msg
    assert "Your answer must follow this format exactly" in msg
    assert RULES_MARKER not in msg


def test_no_outcome_line_without_actions():
    msg = build_env_message(make_config(), round_idx=2)
    assert "A chose" not in msg
    assert "What action would you take" in msg
    assert "choose either action1 or action2" in msg


def test_singular_point_matches_round_one_wording():
    """(D, D) -> P=1 each. Round 1 and the env message must word the same
    event identically."""
    cfg = make_config()
    assert "you got 1 point and A got 1 point." in build_env_message(
        cfg, "D", "D", round_idx=2)
    assert "you got 1 point and they got 1 point." in build_prompt(
        cfg, ["D"], ["D"])


def test_round_clause_paired_with_show_horizon():
    cfg = make_config(show_horizon=True, num_rounds=5)
    assert "This is round 2 of 5." in build_env_message(cfg, "C", "C", round_idx=2)
    assert "This is round" not in build_env_message(
        make_config(), "C", "C", round_idx=2)


def test_round_counter_excludes_fabricated_seed():
    """One counting convention across both builders: real rounds only. A
    fabricated seed is backstory, so the round-1 prompt says round 1 — it
    must not collide with the round-2 env message (it did until 2026-08-09)."""
    for cfg in (make_config(show_horizon=True, num_rounds=5),
                make_config(show_horizon=True, num_rounds=5, reasoning=True)):
        assert "This is round 1 of 5." in build_prompt(cfg, ["C"], ["C"])
        assert "This is round 2 of 5." in build_env_message(
            cfg, "C", "C", round_idx=2)


def test_reasoning_prompt_honors_show_horizon():
    """The reasoning builder ignored show_horizon entirely, so a reasoning
    multi-round episode learned the horizon only from round 2 onward."""
    cfg = make_config(show_horizon=True, num_rounds=5, reasoning=True)
    prompt = build_prompt(cfg, [], [])
    assert "playing a game with agent A for 5 rounds" in prompt
    assert "This is round 1 of 5." in prompt
    off = build_prompt(make_config(reasoning=True), [], [])
    assert "for 5 rounds" not in off
    assert "This is round" not in off


def test_reasoning_closer():
    msg = build_env_message(make_config(reasoning=True), "C", "C", round_idx=2)
    assert "`Action: action1` or `Action: action2`" in msg
    assert "Do not explain" not in msg


def test_restate_rules_arm_adds_only_the_payoff_block():
    plain = build_env_message(make_config(), "C", "C", round_idx=2)
    msg = build_env_message(
        make_config(restate_rules_per_round=True), "C", "C", round_idx=2)
    assert msg.startswith("A chose action1: you got 3 points and A got 3 points.")
    assert RULES_MARKER in msg
    assert HISTORY_MARKER not in msg
    # Difference between the arms is the payoff block and nothing else.
    assert msg.replace(_build_payoff_block(make_config()), "").replace(
        "\n\n", " ").strip() == plain.strip()


# ------------------------------------------------ eval episode loop

def test_run_episode_round2_is_env_message():
    traj = run_episode(make_config(), lambda prompt: "action1")
    round1, round2 = traj.per_round[0]["prompt"], traj.per_round[1]["prompt"]
    assert RULES_MARKER in round1
    assert RULES_MARKER not in round2
    # TFT opens C and mirrors: round 1 is (C, C) -> R=3 each
    assert "A chose action1: you got 3 points and A got 3 points." in round2
    assert HISTORY_MARKER not in round2


def test_run_episode_illegal_freezes_outcome():
    cfg = make_config()
    responses = iter(["no parseable label here!!", "action1", "action1"])
    traj = run_episode(cfg, lambda prompt: next(responses))
    round2 = traj.per_round[1]["prompt"]
    assert round2.startswith(parse_failure_feedback(cfg))
    assert "A chose" not in round2   # state frozen: nothing to report
    # round 3 follows a legal round again and reports its outcome
    assert "A chose" in traj.per_round[2]["prompt"]


# ------------------------------------------------ training parity

def test_game_interaction_builds_same_messages():
    async def run():
        interaction = GameInteraction({})
        state = {
            "game_type": "prisoners_dilemma", "T": 4, "R": 3, "P": 1, "S": 0,
            "opponent": "tit_for_tat", "num_rounds": 3,
            "coop_label": "action1", "defect_label": "action2",
        }
        iid = await interaction.start_interaction(ground_truth=json.dumps(state))
        cfg = interaction._instances[iid]["config"]

        done, msg, reward, _ = await interaction.generate_response(
            iid, [{"role": "assistant", "content": "action1"}])
        assert done is False
        assert reward == 3.0   # (C, C) vs TFT
        assert msg == build_env_message(cfg, "C", "C", round_idx=2)

        done, msg2, reward2, _ = await interaction.generate_response(
            iid, [{"role": "assistant", "content": "???"}])
        assert done is False
        assert msg2 == (
            parse_failure_feedback(cfg) + "\n\n"
            + build_env_message(cfg, round_idx=3)
        )
        await interaction.finalize_interaction(iid)

    asyncio.run(run())
