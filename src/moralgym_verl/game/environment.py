"""Episode configuration and sampling.

This module owns the shared EpisodeConfig (the full specification of one
episode, serialized into datasets and YAML) plus the game-independent
samplers. Everything paradigm-specific — payoff data, scoring, prompt
text — lives with the Game implementations (classic_games.py /
pgg_game.py), resolved via registry.get_game: validation and utility
bounds delegate there.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Tuple


def sample_labels(rng: Optional[random.Random] = None) -> Tuple[str, str]:
    """Sample two distinct randomized action labels, 'action<LETTER>'.

    The 'action' prefix is deliberate. The fixed (Tennant-exact) labels are
    action3/action4, so bare letters would change the label's FORM as well
    as its identity — a fixed-vs-randomized gap would then confound "the
    model tracks a specific symbol" with "the model is thrown by a
    different symbol format". Keeping the prefix varies identity alone.

    Letters rather than digits: the prompt is full of numbers (payoffs,
    points awarded), and a digit label risks being read as a quantity.
    'A' is excluded — the prompt names the opponent 'agent A'.
    """
    r = rng if rng is not None else random
    pair = r.sample("BCDEFGHIJKLMNOPQRSTUVWXYZ", 2)
    return f"action{pair[0]}", f"action{pair[1]}"


@dataclass
class EpisodeConfig:
    """Full specification for one GRPO training episode."""

    game_type: str
    T: int
    R: int
    P: int
    S: int
    opponent: str
    num_rounds: int

    coop_label: str = ""
    defect_label: str = ""
    matrix_layout: int = 0

    # v2 presentation axes — sampled once per episode alongside the fields
    # above (see moralgym_verl.game.prompts.sample_prompt_randomization). They
    # close the sentence-position shortcut empirically found in v1 (see
    # docs/experimental/findings.md).
    #   opener_order / closer_order: order of the two labels in the opener
    #       and closer sentences, independently shuffled when the
    #       label_order axis is randomized.
    #   agent_is_row: True → agent plays rows, the opener says "you are the
    #       row player". False → matrix transposed, phrasing flipped.
    #       Combined with matrix_layout (0–3), covers all 8 D₄ symmetries.
    # Defaults = identity (Tennant-exact). Populated via
    # `sample_prompt_randomization` on the randomization paths.
    opener_order: Tuple[str, str] = ("", "")
    closer_order: Tuple[str, str] = ("", "")
    agent_is_row: bool = True

    # Discloses the total round count in the opener ("for N rounds") and the
    # current round in the closer ("This is round X of N"). Needed for
    # multi-round games — without it the model has no within-episode position
    # signal to condition early-vs-late strategy on, and per-step RTG
    # advantage fails to drive cooperation against TFT (empirical, May 2026).
    # Default off preserves the single-turn v2 behavior.
    show_horizon: bool = False

    # Parser selection for converting the model's raw output into an internal
    # move. See moralgym_verl.game.prompts.parse_action for the two modes.
    #   False (default): lenient — uppercase substring match, rfind tiebreaker.
    #   True: minimal — whitespace strip + exact case-sensitive equality only
    #         (Tennant et al. 2025 protocol). The parser does the bare minimum;
    #         the model must learn to emit the bare label itself. Requires
    #         coupling with a small max_new_tokens (~2) or illegal rates will
    #         be high.
    minimal_parsing: bool = False

    # CoT variant: selects the reasoning format line and the structured
    # parser (`Action: <label>`, no "Do not explain") — see
    # prompts._format_line / parse_action_structured. Pair with
    # max_new_tokens≥64 and stop_strings=null in YAML.
    reasoning: bool = False

    # Hybrid-reasoning templates (Qwen3) branch on apply_chat_template's
    # `enable_thinking`. Unlike `reasoning` (which prompt text is built),
    # this is how the chat wrapper renders. None = don't pass the kwarg,
    # leaving non-hybrid templates (gemma-2) byte-identical.
    enable_thinking: Optional[bool] = None

    # Payoff-representation variant for the middle block of the prompt
    # (see prompts._build_payoff_block):
    #   "matrix" (default) — markdown 2x2 payoff table (Tennant-exact).
    #   "prose"  — the four outcomes as "If you choose X and A chooses Y,
    #              you get p points and A gets q points." sentences in one
    #              flowing paragraph (maximal representational distance
    #              from the grid: outcomes must be bound from syntax).
    #   "list"   — the same sentences as a bulleted list (keeps one visual
    #              slot per outcome; intermediate between matrix and prose).
    # The presentation axes are reinterpreted, not disabled: matrix_layout
    # picks the sentence order (row-major traversal of the same layout,
    # 4 orders — NOT all 24 permutations) and agent_is_row picks the
    # choice-clause subject order. The payoff clause stays you-first in
    # every variant, mirroring the matrix cells' fixed "(your_pts,
    # opp_pts)" order. NOT related to the label_order presentation axis,
    # which shuffles opener/closer label order.
    representation: str = "matrix"

    # Rounds >= 2 env messages in multi-round (conversation) episodes
    # (game_interaction / run_episode; round 1 is always the full prompt,
    # incl. fabricated-seed narration — narrate history exactly when it is
    # NOT in context):
    #   False (default): outcome line + optional round clause + answer-format
    #         line — rules and history are already in the conversation.
    #   True: additionally re-insert the payoff block + closing question
    #         (rules-retention ablation for weaker models).
    # See docs/multi_turn_implementation_plan.md Phase 1.
    restate_rules_per_round: bool = False

    # Public-goods extension (docs/pgg_design.md). game_type="public_goods"
    # replaces the 2x2 payoff matrix with the binary linear PGG: C =
    # contribute the whole endowment, D = keep it. Payoffs are functions of
    # (endowment, share, k_others) via pgg_game.get_score_pgg; the T/R/P/S
    # fields are unused and must be passed as 0 (guarded in
    # PublicGoodsGame.validate_config) so no code path can read a 2x2
    # matrix that doesn't exist. matrix_layout is reinterpreted as the PGG
    # surface facets (see pgg_game._build_pgg_table) and agent_is_row is forced
    # to identity — there are no rows to play. Defaults keep every
    # existing 2x2 call site valid.
    n_players: int = 2
    endowment: Optional[int] = None
    share: Optional[int] = None

    def __post_init__(self) -> None:
        # Paradigm-specific field validation lives with the Game
        # implementation. Lazy import: the game modules import this one.
        from moralgym_verl.game.registry import get_game
        get_game(self.game_type).validate_config(self)
        # Every parser mode infers the move by matching label text
        # (parse_action_structured even falls back to substring-in-token),
        # so a label pair where one contains the other — case-insensitive,
        # as the parsers compare — could silently mis-assign C/D instead of
        # rejecting. No current scheme (action3/action4, sampled
        # action<LETTER>) can produce such a pair; this guards the
        # invariant for future ones.
        if self.coop_label and self.defect_label:
            coop, defect = self.coop_label.upper(), self.defect_label.upper()
            if coop in defect or defect in coop:
                raise ValueError(
                    "action labels must not contain each other "
                    f"(case-insensitive): {self.coop_label!r} / "
                    f"{self.defect_label!r}")

    @property
    def u_max(self) -> int:
        from moralgym_verl.game.registry import get_game
        return get_game(self.game_type).utility_bounds(self)[1]

    @property
    def u_min(self) -> int:
        from moralgym_verl.game.registry import get_game
        return get_game(self.game_type).utility_bounds(self)[0]

    def label_for(self, move: str) -> str:
        """Convert internal move (C/D) to the randomized label."""
        return self.coop_label if move == "C" else self.defect_label

    def move_for(self, label: str) -> Optional[str]:
        """Convert a randomized label back to internal move (C/D)."""
        if label == self.coop_label:
            return "C"
        if label == self.defect_label:
            return "D"
        return None


def sample_episode_config(
    num_rounds_range: Tuple[int, int] = (10, 10),
    payoff_range: Tuple[int, int] = (1, 10),
    opponent_pool: Optional[List[str]] = None,
    game_pool: Optional[List[str]] = None,
    randomize_presentation: bool = True,
) -> EpisodeConfig:
    """Sample a fully random episode configuration.

    Args:
        num_rounds_range: (min_K, max_K) for uniform round count sampling.
        payoff_range: (lo, hi) for payoff value sampling.
        opponent_pool: list of opponent keys to sample from.
            Defaults to all registered opponents.
        game_pool: list of game type keys to sample from.
            Defaults to all game types.
        randomize_presentation: if True, randomize labels and matrix layout.
            Set False for fixed Tennant-style prompts.
    """
    from moralgym_verl.game.classic_games import (
        GAME_ORDERINGS, sample_payoffs,
    )
    from moralgym_verl.game.opponents import OPPONENT_REGISTRY

    games = game_pool or list(GAME_ORDERINGS)
    opponents = opponent_pool or list(OPPONENT_REGISTRY)

    game_type = random.choice(games)
    T, R, P, S = sample_payoffs(game_type, *payoff_range)

    if randomize_presentation:
        cl, dl = sample_labels()
        layout = random.randint(0, 3)
    else:
        cl, dl = "action1", "action2"
        layout = 0

    return EpisodeConfig(
        game_type=game_type,
        T=T, R=R, P=P, S=S,
        opponent=random.choice(opponents),
        num_rounds=random.randint(*num_rounds_range),
        coop_label=cl,
        defect_label=dl,
        matrix_layout=layout,
    )
