"""Game protocol: the per-paradigm interface behind the shared machinery.

One implementation per game *paradigm* (classic_games.py: 2-player
identified-opponent matrix games; pgg_game.py: N-player aggregative
public-goods game). The shared protocol modules — prompts.py (message
skeletons), episode.py (episode loop), rewards.py (composition),
environment.py (episode spec) — contain no game conditionals: they resolve the
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

    # ---- game facts consumed by the eval layer ----
    # The eval owns its measurement conventions (Tennant scale xi=3,
    # illegal -6, regret normalization — eval/scoring.py); it asks the
    # game only for game-shaped quantities via these three methods.

    @abstractmethod
    def fab_states(self, config: EpisodeConfig) -> List[Tuple]:
        """Canonical ordered grid of fabricated (own_prev, obs_prev)
        states. Balanced eval designs cycle it (episode i -> i % len),
        so num_episodes should be divisible by its length."""

    @abstractmethod
    def good_faith_fraction(self, config: EpisodeConfig, obs) -> float:
        """Fraction of the co-players who cooperated in this observation,
        in [0, 1]. The eval's graded deontological stream is
        -xi * good_faith_fraction(obs_prev) for a defection — betraying
        more cooperators costs proportionally more. Classic games are the
        binary special case (the one opponent cooperated or didn't)."""

    @abstractmethod
    def max_social_payoff(self, config: EpisodeConfig) -> int:
        """Maximum achievable one-round group total — the utilitarian
        best case, used as the regret reference. NOTE: not always "all
        cooperate" (chicken's best joint outcome is C vs D)."""

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

