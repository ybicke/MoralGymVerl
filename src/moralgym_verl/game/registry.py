"""game_type -> Game implementation registry.

Separate from base.py so the import graph stays a DAG:
base <- classic_games/pgg_game <- registry <- prompts/episode/rewards.
The three classic game_types share one stateless ClassicGame instance;
a new paradigm registers its implementation here and nowhere else.
"""

from __future__ import annotations

from moralgym_verl.game.base import Game
from moralgym_verl.game.classic_games import ClassicGame
from moralgym_verl.game.pgg_game import PublicGoodsGame

_CLASSIC = ClassicGame()
_PGG = PublicGoodsGame()

GAMES = {
    "prisoners_dilemma": _CLASSIC,
    "stag_hunt": _CLASSIC,
    "chicken": _CLASSIC,
    "public_goods": _PGG,
}


def get_game(game_type: str) -> Game:
    if game_type not in GAMES:
        raise ValueError(
            f"Unknown game type: {game_type}. Choose from {list(GAMES)}"
        )
    return GAMES[game_type]
