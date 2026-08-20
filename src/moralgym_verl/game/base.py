"""Game protocol: the per-paradigm interface behind the shared machinery.

One implementation per game *paradigm* (classic.py: 2-player
identified-opponent matrix games; pgg.py: N-player aggregative
public-goods game). The shared protocol modules — prompts.py (message
skeletons), trajectory.py (episode loop), rewards.py (composition),
environment.py (config) — contain no game conditionals: they resolve the
implementation via registry.get_game(config.game_type) and delegate.

The opponent-side observation type is game-specific and flows through
the whole stack opaquely: a move str ("C"/"D") for classic games, the
int count k of contributing others for the PGG. Prompt builders,
env messages, rewards, and fabricated states all speak this type via
the Game methods, so the shared code never inspects it.

Behavior contract for implementations: prompt text is byte-pinned by the
test suite and RNG draw order is part of reproducibility — implement by
moving existing wording/draws verbatim, never by rephrasing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from moralgym_verl.game.environment import EpisodeConfig


def _pts(n: int) -> str:
    """'1 point' / 'n points' — single owner of the wording used by every
    payoff-mentioning sentence in every game."""
    return f"{n} point" if n == 1 else f"{n} points"


class OpponentSide(ABC):
    """Mutable opponent-side state for one episode.

    `observations` is the per-round observation list the prompt builders
    consume (fabricated seed included, illegal rounds excluded) — the
    generalization of the old opp_history / k_history.
    """

    observations: List

    @abstractmethod
    def draw(self, agent_history: List[str]):
        """Simultaneous draw for the current round, conditioned on
        histories BEFORE the round (the agent's current move is not yet
        appended). Returns the observation; does NOT commit state."""

    @abstractmethod
    def advance(self, obs) -> None:
        """Commit the drawn round to the opponent-side histories."""


class Game(ABC):
    """Stateless per-paradigm strategy object; config carries all data."""

    def validate_config(self, config: EpisodeConfig) -> None:
        """Raise ValueError for config field combinations this paradigm
        forbids. Called from EpisodeConfig.__post_init__."""

    @abstractmethod
    def utility_bounds(self, config: EpisodeConfig) -> Tuple[int, int]:
        """(u_min, u_max) for reward normalization."""

    @abstractmethod
    def score(self, config: EpisodeConfig, action: str, obs) -> int:
        """Agent's points for one round given the opponent observation."""

    @abstractmethod
    def sample_fab_state(self, config: EpisodeConfig) -> Tuple[str, object]:
        """Draw a uniform fabricated (own_prev, obs_prev) seed via module
        `random` (training parity; draw order is part of the contract)."""

    @abstractmethod
    def make_opponents(
        self, config: EpisodeConfig, fab_obs=None
    ) -> OpponentSide:
        """Fresh opponent-side state, optionally seeded with a fabricated
        observation."""

    # ---- prompt text (moved verbatim from prompts.py; byte-pinned) ----

    @abstractmethod
    def opener(self, config: EpisodeConfig) -> str:
        """Opening sentences through '...a certain number of points. '."""

    @abstractmethod
    def payoff_block(self, config: EpisodeConfig) -> str:
        """The representation-dependent middle block, trailing blank line
        included."""

    @abstractmethod
    def history_sentence(
        self, config: EpisodeConfig, agent_history: List[str],
        observations: List,
    ) -> str:
        """History paragraph (fabricated seed) or first-round sentence,
        trailing blank line included."""

    @abstractmethod
    def outcome_line(self, config: EpisodeConfig, agent_action: str, obs) -> str:
        """The env-message outcome sentence for a completed round."""

    # ---- rewards / records ----

    @abstractmethod
    def round_reward(
        self,
        config: EpisodeConfig,
        action: str,
        obs,
        lambda_val: float,
        intrinsic_type: str,
        game_reward_type: str,
        prev_obs,
        agent_prev: Optional[str],
        shaping: Optional[dict],
    ) -> Dict[str, float]:
        """{r_game, r_intrinsic, r_total} for one round."""

    def record_extras(
        self, config: EpisodeConfig, action: str, obs,
        opp_side: OpponentSide,
    ) -> Dict:
        """Game-specific keys merged into the legal per_round entry."""
        return {}

    def illegal_extras(self) -> Dict:
        """Game-specific keys merged into an illegal per_round entry."""
        return {}

    def result_extras(self, fab_obs, per_round: List[Dict]) -> Dict:
        """Game-specific TrajectoryResult fields (fab_opp/fab_k/k_history
        compatibility mapping)."""
        return {}

    def verbose_line(
        self, config: EpisodeConfig, rnd: int, action: str, obs,
        agent_pts: int, agent_history: List[str], observations: List,
        lambda_val: float, intrinsic_type: str, game_reward_type: str,
        shaping: Optional[dict],
    ) -> str:
        """One human-readable line for run_episode(verbose=True)."""
        return f"  R{rnd + 1:>2}: Agent={action}  pts={agent_pts}"
