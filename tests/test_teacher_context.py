"""Tests for the Session-1 teacher-context wrapping (torch-free).

Run on the login node (/usr/bin/python3.11 -m pytest tests/ -x) or inside
the container — no GPU, torch, or transformers needed.
"""

import pathlib

import pytest

from moralgym_verl.eval.teacher_context import (
    DEFAULT_FEEDBACK_TEMPLATE,
    load_reprompt_template,
    wrap_first_user,
    wrap_latest_user,
    wrap_prompt,
)
from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.moral_values import MORAL_VALUE_REGISTRY, get_moral_value
from moralgym_verl.game.prompts import build_prompt

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SDPO_YAML = REPO_ROOT / "configs" / "verl" / "sdpo_pd_tft.yaml"


def _episode_config(**overrides) -> EpisodeConfig:
    defaults = dict(
        game_type="prisoners_dilemma",
        T=4, R=3, P=1, S=0,
        opponent="tit_for_tat",
        num_rounds=5,
        coop_label="action3",
        defect_label="action4",
        opener_order=("action3", "action4"),
        closer_order=("action3", "action4"),
    )
    defaults.update(overrides)
    return EpisodeConfig(**defaults)


# ---------------------------------------------------------------- registry

def test_none_is_empty_and_others_are_not():
    assert MORAL_VALUE_REGISTRY["none"] == ""
    for name, text in MORAL_VALUE_REGISTRY.items():
        if name != "none":
            assert text.strip(), f"{name} has empty text"


def test_wordings_are_label_agnostic():
    # No wording may reference concrete action labels — the model must map
    # principle -> action through the payoff matrix.
    for name, text in MORAL_VALUE_REGISTRY.items():
        assert "action1" not in text and "action2" not in text, name
        assert "action3" not in text and "action4" not in text, name


def test_unknown_name_raises():
    with pytest.raises(ValueError):
        get_moral_value("does_not_exist")


def test_composite_joins_in_given_order():
    a = get_moral_value("deontological")
    b = get_moral_value("forgiveness")
    combined = get_moral_value("deontological+forgiveness")
    assert combined == f"{a}\n\n{b}"
    # Order matters and is preserved.
    assert get_moral_value("forgiveness+deontological") == f"{b}\n\n{a}"


def test_composite_of_three():
    parts = ["deontological", "exploit_resistance", "forgiveness"]
    combined = get_moral_value("+".join(parts))
    assert combined == "\n\n".join(get_moral_value(p) for p in parts)


def test_composite_rejects_none_and_unknown():
    with pytest.raises(ValueError):
        get_moral_value("none+utilitarian")
    with pytest.raises(ValueError):
        get_moral_value("deontological+bogus")


# ---------------------------------------------------------------- template

def test_load_reprompt_template_from_sdpo_yaml():
    template = load_reprompt_template(str(SDPO_YAML))
    for slot in ("{prompt}", "{solution}", "{feedback}"):
        assert slot in template


def test_load_missing_key_raises(tmp_path):
    bad = tmp_path / "no_template.yaml"
    bad.write_text("foo: bar\n")
    with pytest.raises(KeyError):
        load_reprompt_template(str(bad))


# ---------------------------------------------------------------- wrapping

def test_empty_moral_value_is_identity():
    template = load_reprompt_template(str(SDPO_YAML))
    assert wrap_prompt("GAME", template, "") == "GAME"


def test_wrap_mirrors_trainer_format_call():
    # Must equal the exact format() call from ray_trainer._build_teacher_message
    # with an empty solution section and the templated feedback section.
    template = load_reprompt_template(str(SDPO_YAML))
    moral = get_moral_value("deontological")
    expected = template.format(
        prompt="GAME",
        solution="",
        feedback=DEFAULT_FEEDBACK_TEMPLATE.format(feedback_raw=moral),
    )
    assert wrap_prompt("GAME", template, moral) == expected


def test_wrap_contains_prompt_and_moral_value_no_leftover_slots():
    template = load_reprompt_template(str(SDPO_YAML))
    moral = get_moral_value("utilitarian")
    wrapped = wrap_prompt("THE GAME PROMPT", template, moral)
    assert "THE GAME PROMPT" in wrapped
    assert moral in wrapped
    for leftover in ("{prompt}", "{solution}", "{feedback}", "{feedback_raw}"):
        assert leftover not in wrapped


def test_custom_feedback_template():
    template = load_reprompt_template(str(SDPO_YAML))
    wrapped = wrap_prompt("GAME", template, "be nice",
                          feedback_template="\nMoral value to follow:\n{feedback_raw}\n")
    assert "Moral value to follow:\nbe nice" in wrapped


# ---------------------------------------------- conversation mode helper

def test_wrap_latest_user_wraps_only_last_user_turn():
    messages = [
        {"role": "user", "content": "round1 prompt"},
        {"role": "assistant", "content": "round1 answer"},
        {"role": "user", "content": "round2 prompt"},
    ]
    wrapped = wrap_latest_user(messages, lambda p: f"MORAL {p}")
    # Earlier turns untouched (plain student transcript, as in SDPO).
    assert wrapped[0]["content"] == "round1 prompt"
    assert wrapped[1]["content"] == "round1 answer"
    # Only the current user turn is wrapped.
    assert wrapped[2]["content"] == "MORAL round2 prompt"
    # Original list not mutated.
    assert messages[2]["content"] == "round2 prompt"


def test_wrap_first_user_wraps_only_episode_start():
    # Training-exact multi-turn teacher: moral value in the FIRST user
    # turn only (raw_prompt wrapping); all later turns stay plain.
    messages = [
        {"role": "user", "content": "round1 prompt"},
        {"role": "assistant", "content": "round1 answer"},
        {"role": "user", "content": "round2 prompt"},
    ]
    wrapped = wrap_first_user(messages, lambda p: f"MORAL {p}")
    assert wrapped[0]["content"] == "MORAL round1 prompt"
    assert wrapped[1]["content"] == "round1 answer"
    assert wrapped[2]["content"] == "round2 prompt"
    assert messages[0]["content"] == "round1 prompt"   # not mutated


def test_wrap_first_user_requires_leading_user_turn():
    with pytest.raises(ValueError):
        wrap_first_user(
            [{"role": "assistant", "content": "a"},
             {"role": "user", "content": "p"}],
            lambda p: p,
        )
    with pytest.raises(ValueError):
        wrap_first_user([], lambda p: p)


def test_wrap_latest_user_requires_trailing_user_turn():
    with pytest.raises(ValueError):
        wrap_latest_user(
            [{"role": "user", "content": "p"},
             {"role": "assistant", "content": "a"}],
            lambda p: p,
        )
    with pytest.raises(ValueError):
        wrap_latest_user([], lambda p: p)


# ------------------------------------------------------------- integration

@pytest.mark.parametrize("reasoning", [False, True])
def test_real_game_prompt_survives_wrapping(reasoning):
    config = _episode_config(reasoning=reasoning)
    game_prompt = build_prompt(config, [], [])
    template = load_reprompt_template(str(SDPO_YAML))
    wrapped = wrap_prompt(game_prompt, template, get_moral_value("deontological"))
    # Game prompt embedded verbatim -> matrix, labels, and closer intact.
    assert game_prompt in wrapped
    assert "action3" in wrapped and "action4" in wrapped
