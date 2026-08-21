"""Game facts consumed by the eval layer (base.Game hooks + record keys).

Three hooks (fab_states, good_faith_fraction, max_social_payoff) move
eval-relevant game facts behind the Game interface, replacing the
PD-shaped hardcodings in eval/scoring.py; the uniform per-round record
keys (obs, social_payoff) let the eval read round data game-blind.
The max_social_payoff cross-check pins the formulas to the historical
MORAL_MAX constants for the fixed Tennant payoffs.
"""

import pytest

from moralgym_verl.eval.scoring import MORAL_MAX
from moralgym_verl.game.classic_games import FAB_STATES, FIXED_PAYOFFS
from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.episode import run_episode
from moralgym_verl.game.pgg_game import PGG_PARAMS
from moralgym_verl.game.registry import get_game


def make_classic_config(game_type="prisoners_dilemma", **overrides):
    base = dict(
        game_type=game_type,
        opponent="tit_for_tat",
        num_rounds=3,
        coop_label="action1",
        defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
        **FIXED_PAYOFFS[game_type],
    )
    base.update(overrides)
    return EpisodeConfig(**base)


def make_pgg_config(**overrides):
    base = dict(
        game_type="public_goods",
        T=0, R=0, P=0, S=0,
        opponent="conditional_contributor",
        num_rounds=3,
        coop_label="action1",
        defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
        **PGG_PARAMS["canonical"],
    )
    base.update(overrides)
    return EpisodeConfig(**base)


# ------------------------------------------------ fab_states

def test_fab_states_via_interface():
    cfg = make_classic_config()
    assert get_game(cfg.game_type).fab_states(cfg) == FAB_STATES
    pgg = make_pgg_config()
    states = get_game("public_goods").fab_states(pgg)
    assert len(states) == 2 * pgg.n_players
    assert states[0] == ("C", 0) and states[-1] == ("D", 3)


# ------------------------------------------------ good_faith_fraction

def test_good_faith_fraction_classic_binary():
    cfg = make_classic_config()
    game = get_game(cfg.game_type)
    assert game.good_faith_fraction(cfg, "C") == 1.0
    assert game.good_faith_fraction(cfg, "D") == 0.0


def test_good_faith_fraction_pgg_graded_and_n2_reduction():
    cfg = make_pgg_config()
    game = get_game("public_goods")
    assert [game.good_faith_fraction(cfg, k) for k in range(4)] == \
        [0.0, pytest.approx(1 / 3), pytest.approx(2 / 3), 1.0]
    # N=2: k in {0,1} -> exactly the classic binary values
    n2 = make_pgg_config(**PGG_PARAMS["parity"])
    assert game.good_faith_fraction(n2, 0) == 0.0
    assert game.good_faith_fraction(n2, 1) == 1.0


# ------------------------------------------------ max_social_payoff

def test_max_social_payoff_reproduces_moral_max_constants():
    """The config-derived formula must equal the historical MORAL_MAX
    utilitarian constants for the fixed Tennant payoffs — including
    chicken, whose best joint outcome is T+S (one swerves), not 2R."""
    for game_type in FIXED_PAYOFFS:
        cfg = make_classic_config(game_type)
        assert get_game(game_type).max_social_payoff(cfg) == \
            MORAL_MAX["util"][game_type]


def test_max_social_payoff_pgg_regimes():
    game = get_game("public_goods")
    # Dilemma (canonical): all contribute -> N*s*N = 4*5*4 = 80
    assert game.max_social_payoff(make_pgg_config()) == 80
    # Waste null (s < E/N): contributions destroy value -> best is m=0,
    # everyone keeps: N*E = 40 (> N*s*N = 32)
    waste = make_pgg_config(share=2)
    assert game.max_social_payoff(waste) == 40


# ------------------------------------------------ uniform record keys

def test_classic_records_obs_and_social_payoff():
    responses = iter(["action1", "no parseable label!!", "action2"])
    traj = run_episode(make_classic_config(), lambda p: next(responses))
    legal1, illegal, legal2 = traj.per_round
    assert legal1["obs"] == legal1["opp_move"] == "C"
    assert legal1["social_payoff"] == legal1["agent_pts"] + legal1["opp_pts"]
    assert illegal["obs"] is None and illegal["social_payoff"] is None
    assert legal2["obs"] == legal2["opp_move"]


def test_pgg_records_obs_and_social_payoff():
    responses = iter(["action1", "???", "action2"])
    traj = run_episode(make_pgg_config(), lambda p: next(responses))
    legal1, illegal, legal2 = traj.per_round
    assert legal1["obs"] == legal1["k_others"]
    assert legal1["social_payoff"] == legal1["group_payoff"]
    assert illegal["obs"] is None and illegal["social_payoff"] is None
    assert legal2["obs"] == legal2["k_others"]
