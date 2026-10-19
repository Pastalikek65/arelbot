"""Versioned game launch metadata for the future Arel Mini App.

The registry deliberately contains no Telegram transport or web server code.
Each game can expose the same typed state/action contract through callbacks or
the Mini App once initData validation and a feature flag are enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MiniAppGameDefinition:
    key: str
    title: str
    state_key: str
    action_namespace: str
    min_players: int
    max_players: int
    protocol_version: int = 1
    feature_flag: str = "miniapp_games"


MINIAPP_GAMES: Mapping[str, MiniAppGameDefinition] = {
    "uno": MiniAppGameDefinition(
        key="uno", title="UNO", state_key="uno", action_namespace="uno",
        min_players=2, max_players=8,
    ),
}


def get_miniapp_game(key: str) -> MiniAppGameDefinition:
    try:
        return MINIAPP_GAMES[str(key).strip().casefold()]
    except KeyError:
        raise ValueError("miniapp_game_not_registered") from None


def miniapp_manifest() -> list[dict[str, object]]:
    return [
        {
            "key": definition.key,
            "title": definition.title,
            "state_key": definition.state_key,
            "action_namespace": definition.action_namespace,
            "min_players": definition.min_players,
            "max_players": definition.max_players,
            "protocol_version": definition.protocol_version,
            "feature_flag": definition.feature_flag,
        }
        for definition in MINIAPP_GAMES.values()
    ]


__all__ = ["MINIAPP_GAMES", "MiniAppGameDefinition", "get_miniapp_game", "miniapp_manifest"]
