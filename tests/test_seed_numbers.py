"""Episode-level intervals and duplicate guard behind the seed-transfer numbers."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "analysis"))
import seed_numbers as sn  # noqa: E402
from seed_figures import Cell, split  # noqa: E402

CKPT = "/runs/qwen3_8b_sdpo_pd_deon_tft_200_seed{}/global_step_200/actor/lora_adapter"


def make_cell(seed=None, value="none", moves=("CCCCC", "DDDDD")):
    block = {"opponent": "full_contributor",
             "episode_moves": [{"agent": list(m), "fab_state": [None, None]}
                               for m in moves]}
    meta = {"base_model": "Qwen/Qwen3-8B", "moral_value": value,
            "protocol": "multi_round", "representation": "decision_full",
            "checkpoint": CKPT.format(seed) if seed else None}
    cell = Cell(meta, [block])
    cell.path = Path(f"cell_{seed}_{value}")
    return cell


def sample(cell):
    return sn.Sample(cell, sn.strata(cell.first))


def test_standard_error_counts_episodes_not_decisions():
    s = sample(make_cell(moves=["CCCCC"] * 10 + ["DDDDD"] * 10))
    assert s.est == 0.5 and s.n == 20
    # all-or-nothing episodes: sd 0.513 over 20 episodes, not the
    # sqrt(.25 / 100) = 0.05 that 100 independent decisions would give
    assert s.se == pytest.approx(0.5130 / 20 ** 0.5, abs=1e-4)
    assert (s.lo, s.hi) == pytest.approx((0.5 - 1.96 * s.se, 0.5 + 1.96 * s.se), abs=1e-4)


def test_no_spread_gets_wilson_interval():
    s = sample(make_cell(moves=["DDDDD"] * 20))
    assert s.est == 0.0 and s.lo == 0.0
    assert s.hi == pytest.approx(sn.wilson(0.0, 20)[1]) and 0.1 < s.hi < 0.2
    assert s.se > 0                      # so differences against it keep a width


def test_interval_is_clipped_to_unit_range():
    s = sample(make_cell(moves=["CCCCC"] * 19 + ["DDDDD"]))
    assert s.hi == 1.0 and s.lo < s.est


def test_single_round_is_stratified_by_state():
    block = {"episode_moves": [{"agent": [m]} for m in "CDCC"],
             "state_conditioning": {"(C,0)": {"p_C": 1.0, "n": 2},
                                    "(D,0)": {"p_C": 0.5, "n": 2}}}
    groups = sn.strata(block, by_state=True)
    assert [sum(c for c, _ in g) for g in groups] == [2, 1]
    cell = make_cell()
    s = sn.Sample(cell, groups)
    # only the mixed state contributes variance: (1/2)^2 * 0.5 / 2
    assert s.est == 0.75 and s.se == pytest.approx((0.25 * 0.5 / 2) ** 0.5)
    block["state_conditioning"]["(D,0)"]["p_C"] = 1.0
    with pytest.raises(SystemExit):
        sn.strata(block, by_state=True)


def test_versus_counts_seeds_clear_of_the_reference():
    ref = [sample(make_cell(moves=["DDDDD"] * 15 + ["CCCCC"] * 5))]
    high = [sample(make_cell(s, moves=["CCCCC"] * 19 + ["DDDDD"])) for s in (1, 2)]
    same = sample(make_cell(3, moves=["DDDDD"] * 14 + ["CCCCC"] * 6))
    vs = sn.versus(high + [same], ref)
    assert vs["Seeds"] == 2 and vs["Lo"] > 0 and vs["Lo"] < vs["Diff"] < vs["Hi"]


def test_split_rejects_two_cells_for_one_seed():
    cells = [make_cell(), make_cell(value="deontological"), make_cell(1), make_cell(2)]
    assert len(split(cells, [200], "deontological")[2][("SDPO deon", 200)]) == 2
    with pytest.raises(SystemExit, match="duplicate cell"):
        split(cells + [make_cell(1)], [200], "deontological")
    with pytest.raises(SystemExit, match="duplicate cell"):
        split(cells + [make_cell()], [200], "deontological")


def test_unparseable_answers_count_as_not_contributed():
    block = {"episode_moves": [{"agent": ["illegal", "C", "C", "D", "D"]},
                               {"agent": ["C", "D", "D", "D", "D"]},
                               {"agent": ["illegal"] * 5}],
             "per_round": {f"round_{r}": {} for r in range(1, 6)}}
    for r in range(5):  # stored per-round rates over all answers
        moves = [e["agent"][r] for e in block["episode_moves"]]
        block["per_round"][f"round_{r + 1}"] = {"p_C": moves.count("C") / 3, "n": 3}
    cell = make_cell()
    # every episode and every answer counts; unparseable = not contributed
    s = sn.Sample(cell, sn.strata(block))
    assert s.n == 3 and s.est == pytest.approx((2 / 5 + 1 / 5 + 0) / 3)
    # and reproduces the stored all-answers rate
    assert sn.checked(s, sn.all_answers_rate(block)) is s
    r1 = sn.Sample(cell, sn.strata(block, first_round=True))
    assert r1.n == 3 and r1.est == pytest.approx(1 / 3)
    assert sn.all_answers_rate(block, first_round=True) == pytest.approx(1 / 3)


def test_trained_row_interval_is_student_t_over_seed_rates():
    # seed rates 0.4, 0.5, 0.6 -> mean 0.5, sd 0.1, half width 4.303 * 0.1 / sqrt(3)
    seeds = [sample(make_cell(s, moves=["CCCCC"] * k + ["DDDDD"] * (10 - k)))
             for s, k in ((1, 4), (2, 5), (3, 6))]
    st = sn.stats(seeds)
    half = 4.302653 * 0.1 / 3 ** 0.5  # t_{2, 0.975}
    assert (st["Mean"], st["Lo"], st["Hi"]) == (50, sn.pct(0.5 - half), sn.pct(0.5 + half))
    assert sn.cell(seeds) == f"50{{\\scriptsize\\,[{st['Lo']},{st['Hi']}]}}"


def test_figure_band_matches_table_interval():
    from seed_figures import seed_band
    m, lo, hi = seed_band([40.0, 50.0, 60.0])
    half = 4.302653 * 10 / 3 ** 0.5
    assert m == 50 and lo == pytest.approx(50 - half, abs=1e-4) and hi == pytest.approx(50 + half, abs=1e-4)
    assert seed_band([98.0, 99.0, 100.0])[2] == 100   # clipped
    assert seed_band([30.0]) == (30.0, 30.0, 30.0)     # one seed: no band
