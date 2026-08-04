"""Eval config loading, protocol presets, and per-episode config construction.

Three layers, one file:
  - load_config:       YAML -> plain dict (the experiment description)
  - apply_protocol:    named preset -> dict overrides (a stage defined once)
  - build_eval_config: dict + opponent (+ rng) -> EpisodeConfig, the frozen
                       per-episode object run_episode() consumes; called per
                       episode so randomized presentation axes can sample.
"""

from __future__ import annotations

import random
from typing import Dict, Optional

import yaml

from moralgym_verl.game.environment import (
    EpisodeConfig, sample_labels, sample_payoffs,
)
from moralgym_verl.game.prompts import sample_prompt_randomization

# Named experiment protocols (--protocol): a stage's flag bundle in ONE
# executable place. Applied before individual CLI overrides (explicit
# flags still win); the chosen name is recorded in metadata.
PROTOCOL_PRESETS: Dict[str, Dict] = {
    # Single fabricated-history round vs random: per-state policy table.
    "stage1a": {"num_rounds": 1, "game_design": "hist",
                "opponents": ["random"], "transcript": False},
    # Multi-turn dynamics, stateless Markov-1 prompts (legacy protocol).
    "stage1b": {"num_rounds": 5, "game_design": "nohist",
                "transcript": False},
    # Multi-turn with accumulating conversation (verl agent-loop parity).
    "stage1b_transcript": {"num_rounds": 5, "game_design": "nohist",
                           "transcript": True},
}


def load_config(path: str) -> Dict:
    with open(path) as f:
        return yaml.safe_load(f)


def apply_protocol(cfg: Dict, protocol: str) -> None:
    """Apply a PROTOCOL_PRESETS bundle to cfg in place.

    Called before individual CLI overrides so explicit flags still win.
    """
    preset = PROTOCOL_PRESETS[protocol]
    cfg["game"]["num_rounds"] = preset["num_rounds"]
    cfg.setdefault("prompt", {})["game_design"] = preset["game_design"]
    cfg.setdefault("evaluation", {})["transcript"] = preset["transcript"]
    if "opponents" in preset:
        cfg["evaluation"]["opponents"] = preset["opponents"]


def build_eval_config(
    cfg: Dict, opponent: str, rng: Optional[random.Random] = None,
) -> EpisodeConfig:
    """Build an EpisodeConfig for evaluation.

    All presentation draws go through `rng` when given (evaluate() passes
    its dedicated stream so toggling randomization never perturbs other
    draws — fixed and randomized runs stay paired). Falls back to module
    `random` (probe callers; fixed presentation consumes no draws anyway).
    """
    game = cfg["game"]
    prompt_cfg = cfg["prompt"]
    eval_cfg = cfg.get("evaluation", {})

    # Defaults are Tennant-exact (fixed tokens/layout/prose/role/payoffs);
    # training-time randomization flags do NOT propagate to eval. Override
    # per-config under `evaluation:` — tokens/layout/prose/role:
    # fixed|randomize, payoffs: fixed|sample.

    randomize_layout = eval_cfg.get("layout", "fixed") == "randomize"
    randomize_prose = eval_cfg.get("prose", "fixed") == "randomize"
    randomize_role = eval_cfg.get("role", "fixed") == "randomize"

    r = rng if rng is not None else random

    if eval_cfg.get("tokens", "fixed") == "randomize":
        cl, dl = sample_labels(rng=rng)
    else:
        cl, dl = "action3", "action4"

    layout = r.randint(0, 3) if randomize_layout else 0

    if eval_cfg.get("payoffs", "fixed") == "sample":
        T, R, P, S = sample_payoffs(game["type"], rng=rng)
    else:
        p = game["payoffs"]
        T, R, P, S = p["T"], p["R"], p["P"], p["S"]

    opener_order, closer_order, agent_is_row = sample_prompt_randomization(
        cl, dl,
        randomize_prose=randomize_prose,
        randomize_role=randomize_role,
        rng=rng,
    )

    return EpisodeConfig(
        game_type=game["type"],
        T=T, R=R, P=P, S=S,
        opponent=opponent,
        num_rounds=game["num_rounds"],
        coop_label=cl,
        defect_label=dl,
        matrix_layout=layout,
        opener_order=opener_order,
        closer_order=closer_order,
        agent_is_row=agent_is_row,
        show_horizon=prompt_cfg.get("show_horizon", False),
        minimal_parsing=prompt_cfg.get("minimal_parsing", False),
        reasoning=prompt_cfg.get("reasoning", False),
    )
