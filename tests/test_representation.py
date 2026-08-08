"""Payoff-representation variants (matrix | prose | list).

The contract under test: the three representations differ ONLY in the
payoff block; the presentation axes are reinterpreted (layout -> sentence
order, role -> choice-clause subject order) rather than disabled; and the
field plumbs through eval config and the training dataset state dict.
"""

import pytest

from moralgym_verl.eval.config import build_eval_config
from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.prompts import build_prompt
from moralgym_verl.training.dataset import _sample_config_from_yaml


def _config(**overrides) -> EpisodeConfig:
    base = dict(
        game_type="prisoners_dilemma",
        T=4, R=3, P=1, S=0,
        opponent="tit_for_tat",
        num_rounds=5,
        coop_label="action3",
        defect_label="action4",
        matrix_layout=0,
        opener_order=("action3", "action4"),
        closer_order=("action3", "action4"),
        agent_is_row=True,
    )
    base.update(overrides)
    return EpisodeConfig(**base)


# Layout 0, agent-first: row-major traversal CC, CD, DC, DD.
PROSE_SENTENCES = [
    "If you choose action3 and A chooses action3, you each get 3 points.",
    "If you choose action3 and A chooses action4, you get 0 points and A gets 4 points.",
    "If you choose action4 and A chooses action3, you get 4 points and A gets 0 points.",
    "If you choose action4 and A chooses action4, you each get 1 point.",
]


def test_matrix_default_unchanged():
    prompt = build_prompt(_config(), [], [])
    assert "| ------- | ------- | ------- |" in prompt
    assert "(you are the row player, A is the column player)" in prompt


def test_prose_sentences_and_no_table():
    prompt = build_prompt(_config(representation="prose"), [], [])
    assert "|" not in prompt
    assert "row player" not in prompt
    block = prompt.split("The points are awarded as follows: ")[1].split("\n\n")[0]
    assert block == " ".join(PROSE_SENTENCES)  # one flowing paragraph


def test_list_bullets_same_sentences():
    prompt = build_prompt(_config(representation="list"), [], [])
    assert "|" not in prompt
    for s in PROSE_SENTENCES:
        assert f"- {s}" in prompt


def test_outside_payoff_block_identical():
    # The attribution property: matrix and prose prompts are byte-identical
    # before and after the payoff block.
    marker = "The points are awarded as follows"
    tail = "This is the first round."
    matrix = build_prompt(_config(), [], [])
    prose = build_prompt(_config(representation="prose"), [], [])
    assert matrix.split(marker)[0] == prose.split(marker)[0]
    assert matrix.split(tail)[1] == prose.split(tail)[1]


def test_layout_reorders_sentences():
    # Layout 1 = rows [D, C], cols [D, C] -> traversal DD, DC, CD, CC.
    prompt = build_prompt(
        _config(representation="prose", matrix_layout=1), [], [])
    block = prompt.split("The points are awarded as follows: ")[1]
    assert block.startswith(PROSE_SENTENCES[3])  # DD first
    assert block.split("\n\n")[0].endswith(PROSE_SENTENCES[0])  # CC last


def test_role_flips_subject_order_only():
    prompt = build_prompt(
        _config(representation="prose", agent_is_row=False), [], [])
    assert "If A chooses action4 and you choose action3, you get 0 points and A gets 4 points." in prompt
    # Payoff clause stays you-first regardless of role.
    assert "A gets 0 points and you get" not in prompt


def test_reasoning_variant_renders_prose():
    prompt = build_prompt(
        _config(representation="prose", reasoning=True), [], [])
    assert "|" not in prompt
    assert PROSE_SENTENCES[0] in prompt
    assert "Action:" in prompt  # CoT closer intact


def test_unknown_representation_raises():
    with pytest.raises(ValueError, match="representation"):
        build_prompt(_config(representation="bullets"), [], [])


_EVAL_CFG = {
    "game": {"type": "prisoners_dilemma",
             "payoffs": {"T": 4, "R": 3, "P": 1, "S": 0},
             "num_rounds": 5},
    "prompt": {"representation": "prose"},
    "evaluation": {},
}


def test_build_eval_config_passes_representation():
    config = build_eval_config(_EVAL_CFG, opponent="tit_for_tat")
    assert config.representation == "prose"


def test_dataset_state_carries_representation():
    cfg = {
        "game": {"type": "prisoners_dilemma", "num_rounds": 1,
                 "payoffs": {"T": 4, "R": 3, "P": 1, "S": 0}},
        "opponent": {"types": ["tit_for_tat"]},
        "prompt": {"representation": "list"},
        "reward": {},
    }
    config, state = _sample_config_from_yaml(cfg)
    assert config.representation == "list"
    assert state["representation"] == "list"
