"""Public-goods game layer (P0): payoffs, policies, prompts, parity.

The N=2 binary PGG is a PD with T=E+s, R=2s, P=E, S=s (strict iff
E/2 < s < E) and conditional_contributor == tit_for_tat, so the PGG code
path at N=2 must reproduce the PD path: same payoffs, same rewards, same
cooperation metrics, prompts modulo wording (docs/pgg_design.md §3.2).
Parity params (E=10, s=6) -> (T,R,P,S) = (16,12,10,6); the canonical
(E=10, s=5) sits ON the N=2 strictness boundary (R=P) and is not used
here. Episode-loop lockstep coverage mirrors test_env_message.py.
"""

import random

import pytest

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.opponents import (
    conditional_contributor,
    get_group_actions,
    tit_for_tat,
)
from moralgym_verl.game.pgg_game import (
    PGG_PARAMS,
    get_score_pgg,
    group_payoff_pgg,
    pgg_fab_states,
)
from moralgym_verl.game.prompts import (
    _build_payoff_block,
    build_env_message,
    build_prompt,
    parse_failure_feedback,
)
from moralgym_verl.game.episode import run_episode
from moralgym_verl.rewards import compute_round_reward

RULES_MARKER = "The points are awarded as follows"
HISTORY_MARKER = "You have played with this group before"

# Derived-PD payoffs of the N=2 parity params (E=10, s=6).
PD_PARITY = dict(T=16, R=12, P=10, S=6)


def make_pgg_config(**overrides):
    base = dict(
        game_type="public_goods",
        T=0, R=0, P=0, S=0,
        opponent="conditional_contributor",
        num_rounds=1,
        coop_label="action1",
        defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
        **PGG_PARAMS["canonical"],
    )
    base.update(overrides)
    return EpisodeConfig(**base)


def make_parity_pair(num_rounds=1):
    """Matched (PD, N=2 PGG) configs for the parity tests."""
    pd = EpisodeConfig(
        game_type="prisoners_dilemma",
        opponent="tit_for_tat",
        num_rounds=num_rounds,
        coop_label="action1",
        defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
        **PD_PARITY,
    )
    pgg = make_pgg_config(num_rounds=num_rounds, **PGG_PARAMS["parity"])
    return pd, pgg


def label_policy(moves):
    """policy_fn playing a fixed internal-move script."""
    it = iter(moves)
    return lambda prompt: {"C": "action1", "D": "action2"}[next(it)]


# ------------------------------------------------ payoffs and config

def test_score_canonical_table():
    cfg = make_pgg_config()
    assert [get_score_pgg("C", k, cfg) for k in range(4)] == [5, 10, 15, 20]
    assert [get_score_pgg("D", k, cfg) for k in range(4)] == [10, 15, 20, 25]


def test_group_payoff_equals_sum_of_individual_scores():
    cfg = make_pgg_config()
    for m in range(cfg.n_players + 1):
        total = (
            m * get_score_pgg("C", m - 1, cfg)
            + (cfg.n_players - m) * get_score_pgg("D", m, cfg)
        )
        assert group_payoff_pgg(m, cfg) == total


def test_config_validation():
    with pytest.raises(ValueError):
        make_pgg_config(endowment=None)
    with pytest.raises(ValueError):
        make_pgg_config(T=4)          # T/R/P/S must stay 0
    with pytest.raises(ValueError):
        make_pgg_config(agent_is_row=False)  # forced identity


def test_u_bounds_dilemma():
    cfg = make_pgg_config()
    assert cfg.u_max == 25   # keep while all 3 others contribute
    assert cfg.u_min == 5    # contribute alone


def test_fab_states_grid():
    states = pgg_fab_states(4)
    assert len(states) == 8
    assert states[0] == ("C", 0) and states[-1] == ("D", 3)
    # N=2 maps 1:1 onto the 2x2 FAB_STATES grid via k=1 <-> opp "C"
    assert pgg_fab_states(2) == [("C", 0), ("C", 1), ("D", 0), ("D", 1)]


# ------------------------------------------------ contribution policies

def test_conditional_contributor_majority():
    # N=4 bot: its 3 others are the agent + 2 bots; threshold ceil(3/2)=2
    assert conditional_contributor([], [], [[], []]) == "C"           # round 1
    assert conditional_contributor(["C"], ["D"], [["D"], ["C"]]) == "D"  # k=1
    assert conditional_contributor(["C"], ["C"], [["D"], ["C"]]) == "C"  # k=2


def test_conditional_equals_tft_at_n2():
    for agent_hist in ([], ["C"], ["D"], ["C", "D"], ["D", "C", "C"]):
        own = ["C"] * len(agent_hist)
        assert conditional_contributor(own, agent_hist, []) == \
            tit_for_tat(own, agent_hist)


def test_group_actions_homogeneous_dynamics():
    # Agent C + 2 of 3 bots C last round: every bot sees k >= 2 -> all C
    assert get_group_actions(
        "conditional_contributor", ["C"], [["C"], ["C"], ["D"]]
    ) == ["C", "C", "C"]
    # Agent D + 1 of 3 bots C: every bot sees k <= 1 -> all D
    assert get_group_actions(
        "conditional_contributor", ["D"], [["C"], ["D"], ["D"]]
    ) == ["D", "D", "D"]


def test_group_actions_unknown_policy_raises():
    with pytest.raises(ValueError):
        get_group_actions("tit_for_tat", [], [[], [], []])


# ------------------------------------------------ prompt builders

def test_pgg_table_layout0():
    block = _build_payoff_block(make_pgg_config(representation="table"))
    assert "rows: how many of the other 3 players choose action1" in block
    assert "|   | action1 | action2 |" in block
    assert "| 0 | 5 | 10 |" in block
    assert "| 3 | 20 | 25 |" in block


def test_pgg_table_facets():
    # bit 0: k-row order reversed
    rev = _build_payoff_block(
        make_pgg_config(representation="table", matrix_layout=1))
    assert rev.index("| 3 |") < rev.index("| 0 |")
    # bit 1: action columns swapped
    swapped = _build_payoff_block(
        make_pgg_config(representation="table", matrix_layout=2))
    assert "|   | action2 | action1 |" in swapped
    assert "| 0 | 10 | 5 |" in swapped


def test_pgg_representation_names():
    # "matrix" is accepted as a compatibility alias for "table"
    assert _build_payoff_block(make_pgg_config(representation="matrix")) == \
        _build_payoff_block(make_pgg_config(representation="table"))
    with pytest.raises(ValueError):
        _build_payoff_block(make_pgg_config(representation="list"))


def test_pgg_prose_rule():
    block = _build_payoff_block(make_pgg_config(representation="prose"))
    assert "each of the 4 players starts the round with 10 points" in block
    assert "causes each of the 4 players to receive 5 points" in block
    assert ("If you choose action1, you put your 10 points into a common "
            "pool.") in block
    # mention-order facet (bit 1): keep clause first
    swapped = _build_payoff_block(
        make_pgg_config(representation="prose", matrix_layout=2))
    assert swapped.index("action2") < swapped.index("action1")


def test_opener_group_wording_and_no_game_name():
    for rep in ("table", "prose"):
        prompt = build_prompt(make_pgg_config(representation=rep), [], [])
        assert prompt.startswith(
            "You are playing a game in a group with 3 other players.")
        low = prompt.lower()
        for word in ("public goods", "contribute", "cooperate", "defect"):
            assert word not in low
        assert "This is the first round. You have no history with this " \
               "group." in prompt


def test_history_sentence_matches_scoring():
    prompt = build_prompt(make_pgg_config(), ["D"], [2])
    assert ("Last round, you played action2, 2 of the other 3 players "
            "played action1, and you got 20 points.") in prompt


def test_env_message_content():
    cfg = make_pgg_config(num_rounds=3)
    msg = build_env_message(cfg, "D", 2, round_idx=2)
    assert "2 of the other 3 players chose action1: you got 20 points." in msg
    assert "choose either action1 or action2" in msg
    assert RULES_MARKER not in msg
    # k = 0 must still render an outcome (0 is not None)
    zero = build_env_message(cfg, "C", 0, round_idx=2)
    assert "0 of the other 3 players chose action1: you got 5 points." in zero


def test_reasoning_builder_pgg():
    prompt = build_prompt(make_pgg_config(reasoning=True), ["D"], [2])
    assert prompt.startswith(
        "You are playing a game in a group with 3 other players.")
    assert "`Action: action1` or `Action: action2`" in prompt
    assert ("Last round, you played action2, 2 of the other 3 players "
            "played action1, and you got 20 points.") in prompt


# ------------------------------------------------ rewards

def test_graded_deontological():
    cfg = make_pgg_config()
    r = compute_round_reward(
        "D", 2, cfg, lambda_val=3.0, intrinsic_type="deontological",
        opp_prev_action=3)
    assert r["r_game"] == 20.0
    assert r["r_intrinsic"] == pytest.approx(-1.0)
    assert r["r_total"] == pytest.approx(17.0)
    assert compute_round_reward(
        "D", 0, cfg, intrinsic_type="deontological", opp_prev_action=1
    )["r_intrinsic"] == pytest.approx(-1 / 3)
    assert compute_round_reward(
        "C", 0, cfg, intrinsic_type="deontological", opp_prev_action=3
    )["r_intrinsic"] == 0.0


def test_utilitarian_is_group_total():
    r = compute_round_reward(
        "C", 2, make_pgg_config(), game_reward_type="utilitarian",
        intrinsic_type="none")
    assert r["r_game"] == 70.0   # m=3: 4*5*3 + 1*10


def test_tailored_intrinsic_raises_for_pgg():
    with pytest.raises(ValueError):
        compute_round_reward(
            "D", 1, make_pgg_config(),
            intrinsic_type="deontological_tailored", opp_prev_action=1)


# ------------------------------------------------ N=2 parity vs PD

def test_n2_parity_multiround():
    moves = ["C", "D", "C"]
    pd_cfg, pgg_cfg = make_parity_pair(num_rounds=3)
    t_pd = run_episode(pd_cfg, label_policy(moves),
                       lambda_val=3.0, intrinsic_type="deontological")
    t_pgg = run_episode(pgg_cfg, label_policy(moves),
                        lambda_val=3.0, intrinsic_type="deontological")
    for pr_pd, pr_pgg in zip(t_pd.per_round, t_pgg.per_round):
        assert pr_pd["agent_pts"] == pr_pgg["agent_pts"]
        assert pr_pgg["k_others"] == (1 if pr_pd["opp_move"] == "C" else 0)
    assert t_pd.rewards == t_pgg.rewards
    assert t_pd.cooperation_rate == t_pgg.cooperation_rate


@pytest.mark.parametrize("game_reward_type", ["normalized", "utilitarian"])
def test_n2_parity_reward_variants(game_reward_type):
    moves = ["C", "D", "C"]
    pd_cfg, pgg_cfg = make_parity_pair(num_rounds=3)
    t_pd = run_episode(pd_cfg, label_policy(moves),
                       game_reward_type=game_reward_type)
    t_pgg = run_episode(pgg_cfg, label_policy(moves),
                        game_reward_type=game_reward_type)
    assert t_pd.rewards == t_pgg.rewards


def test_n2_parity_fab_states():
    # k_prev=1 <-> opp_prev "C"
    mapping = {("C", "C"): ("C", 1), ("C", "D"): ("C", 0),
               ("D", "C"): ("D", 1), ("D", "D"): ("D", 0)}
    for pd_state, pgg_state in mapping.items():
        for move in ("C", "D"):
            pd_cfg, pgg_cfg = make_parity_pair(num_rounds=1)
            t_pd = run_episode(
                pd_cfg, label_policy([move]), lambda_val=3.0,
                fabricate_history=True, fab_state=pd_state)
            t_pgg = run_episode(
                pgg_cfg, label_policy([move]), lambda_val=3.0,
                fabricate_history=True, fab_state=pgg_state)
            assert t_pd.per_round[0]["agent_pts"] == \
                t_pgg.per_round[0]["agent_pts"]
            assert t_pd.rewards == t_pgg.rewards


def test_n2_table_contains_derived_pd_payoffs():
    _, pgg_cfg = make_parity_pair()
    block = _build_payoff_block(pgg_cfg)
    for v in PD_PARITY.values():   # 16, 12, 10, 6
        assert f" {v} " in block


# ------------------------------------------------ episode-loop lockstep

def test_run_episode_round2_is_env_message():
    traj = run_episode(make_pgg_config(num_rounds=3), lambda p: "action1")
    r1, r2 = traj.per_round[0]["prompt"], traj.per_round[1]["prompt"]
    assert RULES_MARKER in r1
    assert RULES_MARKER not in r2
    # No fabricated history: conditional bots open C -> k=3; agent C -> 20
    assert "3 of the other 3 players chose action1: you got 20 points." in r2
    assert HISTORY_MARKER not in r2


def test_run_episode_illegal_freezes_outcome():
    cfg = make_pgg_config(num_rounds=3)
    responses = iter(["no parseable label here!!", "action1", "action1"])
    traj = run_episode(cfg, lambda p: next(responses))
    assert traj.per_round[0]["k_others"] is None
    r2 = traj.per_round[1]["prompt"]
    assert r2.startswith(parse_failure_feedback(cfg))
    assert "players chose" not in r2   # state frozen: nothing to report
    assert "players chose" in traj.per_round[2]["prompt"]


def test_k_history_and_pair_metrics():
    traj = run_episode(make_pgg_config(num_rounds=2), lambda p: "action2")
    # Round 1: bots open C (k=3). Round 2: each bot saw 2 of its 3 others
    # contribute (agent D + 2 bots C) -> majority -> all C again (k=3).
    assert traj.k_history == [3, 3]
    assert traj.opponent_moves == [None, None]
    assert traj.cooperation_rate == 0.0
    assert traj.mutual_cooperation_rate is None   # pair metrics are 2x2-only


def test_pgg_fab_sampling_bounds():
    random.seed(0)
    traj = run_episode(make_pgg_config(), lambda p: "action1",
                       fabricate_history=True)
    assert traj.fab_agent in ("C", "D")
    assert 0 <= traj.fab_k <= 3
    assert HISTORY_MARKER in traj.per_round[0]["prompt"]
