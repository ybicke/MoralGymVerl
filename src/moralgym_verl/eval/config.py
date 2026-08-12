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
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]


def git_provenance() -> Optional[str]:
    """Short commit hash of the repo the eval code ran from, with a
    '-dirty' suffix when the working tree had uncommitted changes.
    None when git or the repo is unavailable (e.g. stripped container) —
    provenance is best-effort and must never fail an eval run."""
    try:
        rev = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        return f"{rev}-dirty" if status else rev
    except Exception:
        return None

from moralgym_verl.game.environment import (
    EpisodeConfig, sample_labels, sample_payoffs,
)
from moralgym_verl.game.prompts import sample_prompt_randomization

# Named experiment protocols (--protocol): a protocol's flag bundle in
# ONE executable place. Applied before individual CLI overrides (explicit
# flags still win); the chosen name is recorded in metadata.
# (Old campaign-plan names, in pre-2026-08 results: stage1a = single_round,
# stage1b = the REMOVED stateless multi-round protocol, stage1b_transcript =
# today's multi_round. Since 2026-08-08 multi-round episodes are always
# conversations — the stateless Markov-1 multi-round protocol was deleted;
# pre-change multi-round results are not reproducible from HEAD.)
PROTOCOL_PRESETS: Dict[str, Dict] = {
    # Single fabricated-history round vs random: per-state policy table.
    "single_round": {"num_rounds": 1, "game_design": "hist",
                     "opponents": ["random"]},
    # Multi-round dynamics: the conversation accumulates in context
    # (verl agent-loop training parity), rounds >= 2 get env messages.
    "multi_round": {"num_rounds": 5, "game_design": "nohist"},
}


# Presentation randomization (--presentation): the five surface axes and
# the value each takes when randomized. Unlike PROTOCOL_PRESETS these are
# COMPOSABLE rather than named bundles — a fixed set of presets would need
# one name per subset (2^5). The spec is '+'-separated, mirroring the
# moral-value composite convention: 'labels+layout', 'all', 'fixed'.
#
# Note payoffs is 'sample' where the others are 'randomize' — it draws a
# new (T,R,P,S) tuple satisfying the game's ordering, not a permutation of
# a fixed one.
PRESENTATION_AXES: Dict[str, str] = {
    "labels": "randomize",       # action-label symbols (action3/4 -> A-Z)
    "layout": "randomize",       # geometric permutation of the 2x2 grid
    "label_order": "randomize",  # label order in opener/closer sentences
    "role": "randomize",         # agent_is_row coin flip (transposes)
    "payoffs": "sample",         # resample T,R,P,S per episode
}

# Named specs, for the cases worth naming. The distinction that matters is
# SURFACE vs CONTENT: the first four axes re-render an identical game (a
# behavioral change under them means the model is reading position or
# symbol, not payoff structure), whereas 'payoffs' resamples the game
# itself — same ordering, different magnitudes. That is generalization
# across payoff instances, a different claim, and it does not pair with
# the fixed arm the way the surface axes do (different numbers = genuinely
# different decisions, not the same decision re-rendered).
PRESENTATION_PRESETS: Dict[str, Tuple[str, ...]] = {
    "fixed_representation": (),
    "surface_randomization": ("labels", "layout", "label_order", "role"),
    "full_randomization": tuple(PRESENTATION_AXES),
}


def resolve_presentation(spec: str) -> set:
    """Which axes a spec randomizes. A PRESENTATION_PRESETS name, or a
    '+'-separated list of axis names ('labels+role'). Raises ValueError on
    an unknown name — shared by the CLI and by submit-time sweep
    validation, so a typo fails identically in both places."""
    parts = [p.strip() for p in str(spec).split("+") if p.strip()]
    if len(parts) == 1 and parts[0] in PRESENTATION_PRESETS:
        return set(PRESENTATION_PRESETS[parts[0]])
    unknown = [p for p in parts if p not in PRESENTATION_AXES]
    if unknown:
        raise ValueError(
            f"unknown presentation spec {spec!r}: {unknown} is neither a "
            f"preset {sorted(PRESENTATION_PRESETS)} nor an axis "
            f"{sorted(PRESENTATION_AXES)} (axes join with '+')"
        )
    return set(parts)


def apply_presentation(cfg: Dict, spec: str) -> None:
    """Apply a presentation-randomization spec to cfg in place.

    Every axis is written explicitly (not just the named ones) so the spec
    fully determines the presentation block — a cell can never inherit a
    randomized axis from the config it happens to load.

    Called after apply_protocol and before the individual --eval-* flags,
    so an explicit flag still overrides the spec (same precedence rule as
    protocol).
    """
    chosen = resolve_presentation(spec)

    evaluation = cfg.setdefault("evaluation", {})
    for axis, randomized_value in PRESENTATION_AXES.items():
        evaluation[axis] = randomized_value if axis in chosen else "fixed"


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

    # Defaults are Tennant-exact (fixed labels/layout/label_order/role/
    # payoffs); training-time randomization flags do NOT propagate to eval.
    # Override per-config under `evaluation:` — labels/layout/label_order/
    # role: fixed|randomize, payoffs: fixed|sample.

    randomize_layout = eval_cfg.get("layout", "fixed") == "randomize"
    randomize_label_order = eval_cfg.get("label_order", "fixed") == "randomize"
    randomize_role = eval_cfg.get("role", "fixed") == "randomize"

    r = rng if rng is not None else random

    if eval_cfg.get("labels", "fixed") == "randomize":
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
        randomize_label_order=randomize_label_order,
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
        enable_thinking=prompt_cfg.get("enable_thinking"),
        representation=prompt_cfg.get("representation", "matrix"),
        restate_rules_per_round=prompt_cfg.get("restate_rules_per_round", False),
    )
