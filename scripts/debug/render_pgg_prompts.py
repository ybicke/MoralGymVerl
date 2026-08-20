"""Render PGG prompts for eyeballing / browser paste-testing.

Prints the canonical N=4 cell across the 8 fabricated states for both
representations, an env message, and one compliance-null prompt (s > E,
contributing dominant — the payoff-comprehension positive control).
Runs on the login node: /usr/bin/python3.11 with PYTHONPATH=src.

    cd ~/MoralGymVerl && PYTHONPATH=src /usr/bin/python3.11 \
        scripts/debug/render_pgg_prompts.py
"""

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.pgg import PGG_PARAMS, pgg_fab_states
from moralgym_verl.game.prompts import build_env_message, build_prompt


def make_config(representation, **overrides):
    base = dict(
        game_type="public_goods",
        T=0, R=0, P=0, S=0,
        opponent="conditional_contributor",
        num_rounds=1,
        coop_label="actionB",
        defect_label="actionF",
        opener_order=("actionB", "actionF"),
        closer_order=("actionB", "actionF"),
        representation=representation,
        **PGG_PARAMS["canonical"],
    )
    base.update(overrides)
    return EpisodeConfig(**base)


def banner(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


for rep in ("table", "prose"):
    cfg = make_config(rep)
    banner(f"{rep.upper()} — first round, no history")
    print(build_prompt(cfg, [], []))
    for own, k in pgg_fab_states(cfg.n_players):
        banner(f"{rep.upper()} — fabricated state (own_prev={own}, k_prev={k})")
        print(build_prompt(cfg, [own], [k]))

banner("ENV MESSAGE — round 2 after (D, k=2)")
print(build_env_message(make_config("table", num_rounds=3), "D", 2,
                        round_idx=2))

banner("COMPLIANCE NULL (E=10, s=12) — table, state (D, k=2)")
print(build_prompt(make_config("table", share=12), ["D"], [2]))
