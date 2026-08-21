"""PGG eval integration: metrics vocabulary (docs/pgg_design.md §4).

A balanced 8-state single-round mini-eval with a deterministic policy
(contribute iff k_prev >= 2) vs full contributors (k_now always 3), so
every expected value below is hand-computed:

  per own_prev, P(C) over k = [0, 0, 1, 1]  -> k_slope = 0.4
  4 C-rounds (m=4, group 80) + 4 D-rounds (m=3, group 70)
    -> group_efficiency = 75/80
  freeride (outcome): D with k_now=3 -> weight 1 each -> 4/8 = 0.5
  freeride (kprev):   D at k_prev 0,1 per own -> (0+1/3)*2 / 8 = 1/12
  sucker: never contributes alone (k_now=3 always; k_prev>=2 when C) -> 0
"""

import pytest

from moralgym_verl.eval.metrics import aggregate_rollout_metrics
from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.episode import run_episode
from moralgym_verl.game.pgg_game import PGG_PARAMS, pgg_fab_states


def make_pgg_config(**overrides):
    base = dict(
        game_type="public_goods",
        T=0, R=0, P=0, S=0,
        opponent="full_contributor",
        num_rounds=1,
        coop_label="action1",
        defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
        **PGG_PARAMS["canonical"],
    )
    base.update(overrides)
    return EpisodeConfig(**base)


def run_balanced_mini_eval():
    cfg = make_pgg_config()
    results = []
    for own, k in pgg_fab_states(cfg.n_players):
        response = "action1" if k >= 2 else "action2"
        results.append(run_episode(
            cfg, lambda prompt, r=response: r,
            fabricate_history=True, fab_state=(own, k),
        ))
    return aggregate_rollout_metrics(results, "full_contributor", 8)


def test_pgg_block_curve_and_slope():
    out = run_balanced_mini_eval()
    block = out["pgg"]
    curve = block["cond_contribution_curve"]
    assert set(curve) == {"C", "D"}
    for own in ("C", "D"):
        assert [curve[own][str(k)]["p_C"] for k in range(4)] == [0, 0, 1, 1]
        assert curve[own]["2"]["n"] == 1
        assert block["k_slope"][own] == pytest.approx(0.4)


def test_pgg_block_rates_and_efficiency():
    out = run_balanced_mini_eval()
    block = out["pgg"]
    assert block["max_social_payoff"] == 80
    assert block["group_efficiency"] == pytest.approx(75 / 80)
    assert block["sucker_rate"] == 0.0
    assert block["sucker_rate_kprev"] == 0.0
    assert block["freeride_on_contributors"] == pytest.approx(0.5)
    assert block["freeride_on_contributors_kprev"] == pytest.approx(1 / 12)


def test_pgg_generic_keys_and_2x2_metrics_null():
    out = run_balanced_mini_eval()
    # Generic state keys render with k ints; pair-based 2x2 metrics are
    # None (no identified opponent), their legacy conditional blocks
    # empty (n=0) rather than crashing on int observations.
    assert "(C,2)" in out["state_conditioning"]
    assert out["mutual_cooperation_rate"] is None
    assert out["cond_given_opp_c"]["n"] == 0
    assert out["cooperation_rate"] == pytest.approx(0.5)
    # Reward streams flow: deon regret reflects the two k_prev>=2 keeps?
    # (policy contributes there) -> no betrayal rounds at all except none;
    # D happens only at k_prev in {0,1}: fractions 0/3 and 1/3 per own.
    # mean_r_deon = -3 * (0 + 1/3 + 0 + 1/3) / 8 = -0.25
    assert out["mean_r_deon"] == pytest.approx(-0.25)


def test_classic_output_shape_unchanged():
    cfg = EpisodeConfig(
        game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
        opponent="tit_for_tat", num_rounds=2,
        coop_label="action1", defect_label="action2",
        opener_order=("action1", "action2"),
        closer_order=("action1", "action2"),
    )
    results = [run_episode(cfg, lambda p: "action1")]
    out = aggregate_rollout_metrics(results, "tit_for_tat", 1)
    assert out["pgg"] is None
    assert out["cond_given_opp_c"]["n"] == 1   # round 2 conditioned on C
    assert out["mutual_cooperation_rate"] == 1.0
