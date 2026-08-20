"""Mock-model integration test for the behavioral eval pipeline.

Covers the plumbing BETWEEN the unit-tested parts: protocol presets ->
config, balanced fabricated-state allocation -> run_episode, teacher
wrapper -> policy, scripted moves -> exact aggregate metrics + metadata.
No GPU, no model: load_model_for_eval and make_policy_fn are monkeypatched
with a scripted policy.

Expected numbers are HAND-COMPUTED from the episode script below; each
assertion goes red under the matching mutation (preset not applied,
balanced cycle dropped, wrapper skipped, freeze convention flipped).
"""

import copy

import pytest

import moralgym_verl.eval.behavioral as behavioral

BASE_CFG = {
    "seed": 42,
    "policy": {"model_name": "mock-model"},
    "game": {"type": "prisoners_dilemma",
             "payoffs": {"T": 4, "R": 3, "P": 1, "S": 0},
             "num_rounds": 5},
    "prompt": {"game_design": "nohist", "reasoning": False,
               "minimal_parsing": False, "show_horizon": False},
    "evaluation": {"opponents": ["tit_for_tat"], "num_episodes": 8,
                   "temperature": 1.0, "max_new_tokens": 10,
                   "state_design": "balanced"},
    "reward": {"lambda": 0.0, "intrinsic": "deontological",
               "game_reward": "raw"},
    "teacher": {"moral_value": "none",
                "template_source": "configs/verl/sdpo_pd_tft.yaml"},
}


class ScriptedPolicy:
    """Returns pre-scripted responses in call order; records every prompt."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.wrapper = None

    def factory(self, model, tokenizer, max_new_tokens=10, temperature=1.0,
                raw_log=None, prompt_wrapper=None, enable_thinking=None):
        self.wrapper = prompt_wrapper

        def policy_fn(prompt):
            if prompt_wrapper is not None:
                prompt = prompt_wrapper(prompt)
            self.prompts.append(prompt)
            return self.responses.pop(0)

        return policy_fn


@pytest.fixture()
def scripted(monkeypatch):
    def _install(responses):
        pol = ScriptedPolicy(responses)
        monkeypatch.setattr(behavioral, "load_model_for_eval",
                            lambda ckpt, base: (object(), object()))
        monkeypatch.setattr(behavioral, "make_policy_fn", pol.factory)
        return pol
    return _install


def test_apply_protocol_single_round():
    cfg = copy.deepcopy(BASE_CFG)
    behavioral.apply_protocol(cfg, "single_round")
    assert cfg["game"]["num_rounds"] == 1
    assert cfg["prompt"]["game_design"] == "hist"
    assert cfg["evaluation"]["opponents"] == ["random"]


def test_single_round_end_to_end_metrics(scripted):
    """8 single_round episodes; balanced cycle fixes the state order to
    CC CD DC DD CC CD DC DD. Scripted moves (lenient parser:
    action3->C, action4->D, 'garbled'->illegal):

        ep:      1   2   3        4   5   6   7   8
        state:   CC  CD  DC       DD  CC  CD  DC  DD
        move:    C   D   illegal  C   D   C   C   D

    Hand-computed expectations below.
    """
    pol = scripted(["action3", "action4", "garbled", "action3",
                    "action4", "action3", "action3", "action4"])
    cfg = copy.deepcopy(BASE_CFG)
    behavioral.apply_protocol(cfg, "single_round")

    results = behavioral.evaluate(cfg, checkpoint=None)
    assert len(pol.prompts) == 8          # one decision per episode
    assert pol.wrapper is None            # moral_value none -> no wrapper
    r = results[0]

    assert r["opponent"] == "random"
    assert r["parse_failure_rate"] == pytest.approx(1 / 8)
    # Episode coop rates over LEGAL moves: 1,0,1,0,1,1,0 — ep 3 has no
    # legal move, so it is EXCLUDED (not counted as 0) and reported in
    # num_episodes_all_illegal.
    assert r["cooperation_rate"] == pytest.approx(4 / 7)
    assert r["num_episodes_all_illegal"] == 1
    assert r["num_episodes_no_legal_pairs"] == 1

    sc = r["state_conditioning"]
    assert sc["(C,C)"] == {"p_C": 0.5, "p_D": 0.5, "p_illegal": 0.0, "n": 2}
    assert sc["(C,D)"] == {"p_C": 0.5, "p_D": 0.5, "p_illegal": 0.0, "n": 2}
    assert sc["(D,C)"] == {"p_C": 0.5, "p_D": 0.0, "p_illegal": 0.5, "n": 2}
    assert sc["(D,D)"] == {"p_C": 0.5, "p_D": 0.5, "p_illegal": 0.0, "n": 2}

    # Pooled conditionals: opp_prev=C states (CC, DC) hold [C, illegal, D, C].
    assert r["cond_given_opp_c"]["n"] == 4
    assert r["cond_given_opp_c"]["p_C"] == pytest.approx(2 / 4)
    assert r["cond_given_opp_c"]["p_illegal"] == pytest.approx(1 / 4)

    # Presentation is fixed Tennant-exact by default — written once at the
    # result level (per-episode only in randomized-presentation runs).
    pres = r["presentation"]
    assert (pres["coop_label"], pres["defect_label"]) == ("action3", "action4")
    assert pres["matrix_layout"] == 0
    assert "presentation" not in r["episode_moves"][0]


def test_all_illegal_run_reports_missing_data(scripted):
    """Llama-parse-fail scenario: every response garbled. Rates must be
    None (missing data), not 0.0 ("always defected"), with the exclusion
    counts reported — and the console summary must not crash on None."""
    pol = scripted(["garbled"] * 4)
    cfg = copy.deepcopy(BASE_CFG)
    behavioral.apply_protocol(cfg, "single_round")
    cfg["evaluation"]["num_episodes"] = 4

    r = behavioral.evaluate(cfg, checkpoint=None)[0]
    assert r["parse_failure_rate"] == 1.0
    assert r["cooperation_rate"] is None
    assert r["cooperation_rate_std"] is None
    assert r["mutual_cooperation_rate"] is None
    assert r["exploitation_rate"] is None
    assert r["sucker_rate"] is None
    assert r["mutual_defection_rate"] is None
    assert r["num_episodes_all_illegal"] == 4
    assert r["num_episodes_no_legal_pairs"] == 4
    # Per-state table keeps illegal as its own category, n intact.
    assert all(v["p_illegal"] == 1.0 and v["n"] == 1
               for v in r["state_conditioning"].values())


def test_teacher_wrapper_reaches_policy(scripted):
    pol = scripted(["action3"] * 4)
    cfg = copy.deepcopy(BASE_CFG)
    behavioral.apply_protocol(cfg, "single_round")
    cfg["evaluation"]["num_episodes"] = 4
    cfg["teacher"]["moral_value"] = "deontological"

    behavioral.evaluate(cfg, checkpoint=None)
    assert callable(pol.wrapper)
    # Wrapper embeds the game prompt inside the SDPO template with the value.
    wrapped = pol.wrapper("GAME_PROMPT_SENTINEL")
    assert "GAME_PROMPT_SENTINEL" in wrapped
    assert "exploit" in wrapped            # deontological wording
    # And the prompts the policy actually saw carry the value too.
    assert all("exploit" in p for p in pol.prompts)


def test_balanced_allocation_is_exact(scripted):
    # 12 episodes -> exactly 3 per state, deterministically.
    pol = scripted(["action3"] * 12)
    cfg = copy.deepcopy(BASE_CFG)
    behavioral.apply_protocol(cfg, "single_round")
    cfg["evaluation"]["num_episodes"] = 12

    r = behavioral.evaluate(cfg, checkpoint=None)[0]
    assert {k: v["n"] for k, v in r["state_conditioning"].items()} == {
        "(C,C)": 3, "(C,D)": 3, "(D,C)": 3, "(D,D)": 3}
