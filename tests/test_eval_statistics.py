"""Statistics-hygiene guarantees of the eval design.

Three properties, each load-bearing for paired comparisons:
1. run_episode honors an explicit fab_state (balanced design plumbing).
2. The FAB_STATES cycle allocates exactly n/4 episodes per state.
3. Presentation sampling through a dedicated rng leaves the module
   `random` stream untouched, so toggling randomization axes cannot
   perturb any other draw (fixed vs randomized runs stay paired).
"""

import random

from moralgym_verl.game.classic_games import sample_payoffs
from moralgym_verl.game.environment import EpisodeConfig, sample_labels
from moralgym_verl.game.episode import FAB_STATES, run_episode

CFG = EpisodeConfig(
    game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
    opponent="always_cooperate", num_rounds=1,
    coop_label="action3", defect_label="action4",
)


def _capture_policy(prompts):
    def policy(prompt):
        prompts.append(prompt)
        return "Reasoning.\nAction: action3"
    return policy


class TestFabState:
    def test_explicit_state_lands_in_prompt(self):
        for fab_agent, fab_opp in FAB_STATES:
            prompts = []
            run_episode(CFG, _capture_policy(prompts),
                        fabricate_history=True,
                        fab_state=(fab_agent, fab_opp))
            label = {"C": "action3", "D": "action4"}
            expected = (f"you played {label[fab_agent]} and "
                        f"they played {label[fab_opp]}")
            assert expected in prompts[0]

    def test_explicit_state_consumes_no_rng(self):
        random.seed(0)
        before = random.getstate()
        run_episode(CFG, _capture_policy([]),
                    fabricate_history=True, fab_state=("C", "D"))
        assert random.getstate() == before

    def test_default_still_samples(self):
        random.seed(0)
        before = random.getstate()
        run_episode(CFG, _capture_policy([]), fabricate_history=True)
        assert random.getstate() != before  # legacy path draws


class TestBalancedCycle:
    def test_exact_allocation(self):
        n = 200
        counts = {}
        for i in range(n):
            state = FAB_STATES[i % len(FAB_STATES)]
            counts[state] = counts.get(state, 0) + 1
        assert set(counts.values()) == {n // 4}
        assert set(counts) == set(FAB_STATES)


class TestPresentationRngIsolation:
    def test_dedicated_rng_leaves_module_stream_untouched(self):
        random.seed(42)
        before = random.getstate()
        rng = random.Random(42 + 1_000_003)
        sample_labels(rng=rng)
        sample_payoffs("prisoners_dilemma", rng=rng)
        assert random.getstate() == before

    def test_dedicated_rng_reproducible(self):
        a = random.Random(7)
        b = random.Random(7)
        assert sample_labels(rng=a) == sample_labels(rng=b)
        assert (sample_payoffs("prisoners_dilemma", rng=a)
                == sample_payoffs("prisoners_dilemma", rng=b))

    def test_default_falls_back_to_module_random(self):
        random.seed(3)
        first = sample_labels()
        random.seed(3)
        assert sample_labels() == first
