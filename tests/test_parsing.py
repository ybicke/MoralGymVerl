"""Strict reasoning-trace parser tests (torch-free).

Regression cases taken from real Stage 1a traces (2026-07-14) that the
old parser (optional separator + lenient fallback) mishandled.
"""

import pytest

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.prompts_reasoning import parse_action_structured


CFG = EpisodeConfig(
    game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
    opponent="random", num_rounds=1,
    coop_label="action3", defect_label="action4",
)


def parse(text):
    return parse_action_structured(text, CFG)


# --- well-formed Action lines -> parsed ---

def test_plain_action_line():
    assert parse("Reasoning...\n\nAction: action3") == "C"
    assert parse("Reasoning...\n\nAction: action4") == "D"


def test_markdown_variants():
    assert parse("...\n**Action: action3**") == "C"
    assert parse("...\n**Action:** action4") == "D"
    assert parse("...\nAction: **action3**") == "C"
    assert parse("...\nAction - action4.") == "D"


def test_trailing_prose_mention_does_not_hijack():
    # Real Stage 1a failure: clean Action line followed by prose that
    # mentions a label — old regex (optional separator) matched the
    # trailing mention and voided the structured parse.
    trace = ("**Action: action3** is the best choice as it has the highest "
             "potential for a combined score if A also chooses action3.")
    assert parse(trace) == "C"
    trace2 = ("Action: action4\n\nThis avoids the risk of action3 being "
              "exploited.")
    assert parse(trace2) == "D"


def test_last_action_line_wins():
    trace = "Action: action3... wait, reconsidering. Action: action4"
    assert parse(trace) == "D"


# --- no well-formed Action line -> illegal (None), never guessed ---

def test_prose_only_is_illegal():
    # Real Stage 1a misparse case: argues for action4, never emits the
    # format, last prose mention is action3 — old lenient parser returned
    # C (wrong). Strict parser: no signal instead of a wrong one.
    trace = ("Action4 guarantees a minimum of 2 points combined, which is "
             "better than the maximum of 6 with action3 (since it includes "
             "the possibility of 0 points for you).")
    assert parse(trace) is None


def test_spaced_label_is_illegal():
    assert parse("I will choose Action 3 for reliability.") is None


def test_empty_and_garbage_are_illegal():
    assert parse("") is None
    assert parse("I refuse to play this game.") is None


# --- randomized labels parse identically to fixed ones -------------------
# The presentation-robustness sweep compares a fixed-label arm against a
# randomized-label arm, so any difference in PARSEABILITY between the two
# would show up as a behavioral gap that is really a parser artifact.
# (This is why sample_labels emits 'action<LETTER>' rather than a bare
# letter: under bare labels "Action: L" parsed while "Action: 3" did not,
# so the randomized arm tolerated abbreviations the fixed arm rejected.)

_LABEL_ARMS = [("action3", "action4"), ("actionL", "actionF")]

_RESPONSE_SHAPES = [
    ("plain",            "Reasoning here.\nAction: {c}",                  "C"),
    ("markdown",         "Blah.\n**Action:** {d}",                        "D"),
    ("dash separator",   "Blah.\nAction - {c}",                           "C"),
    ("trailing punct",   "Blah.\nAction: {c}.",                           "C"),
    ("prose then line",  "I could pick {d}, but no.\nAction: {c}",        "C"),
    ("prose only",       "I will choose {c} because it is fair.",         None),
    ("abbreviated",      "Blah.\nAction: {abbrev}",                       None),
]


@pytest.mark.parametrize("coop,defect", _LABEL_ARMS)
@pytest.mark.parametrize("shape,template,expected", _RESPONSE_SHAPES)
def test_parse_is_label_agnostic(coop, defect, shape, template, expected):
    config = EpisodeConfig(
        game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
        opponent="random", num_rounds=1,
        coop_label=coop, defect_label=defect,
    )
    response = template.format(c=coop, d=defect,
                               abbrev=coop.replace("action", ""))
    assert parse_action_structured(response, config) == expected, shape
