"""Deterministic, serializable Batak trick-round domain model.

Telegram and persistence concerns stay in ``BatakService``.  This module only
mutates a JSON-compatible state document, which makes replay and restart tests
possible without a live Telegram client.
"""

from __future__ import annotations

import random
from uuid import uuid4
from typing import Any


class BatakRound:
    SUITS = ("♠", "♥", "♦", "♣")
    RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
    VALUES = {rank: index for index, rank in enumerate(RANKS, start=2)}
    MAX_PLAYERS = 4

    @classmethod
    def lobby(cls, owner_id: int, owner_name: str) -> dict[str, Any]:
        return {
            "version": 2,
            "phase": "lobby",
            "owner_id": int(owner_id),
            "players": [{"id": int(owner_id), "name": str(owner_name)[:80]}],
        }

    @classmethod
    def join(cls, state: dict[str, Any], user_id: int, user_name: str) -> str:
        if state.get("phase") != "lobby":
            raise ValueError("game_started")
        players = state.setdefault("players", [])
        if any(int(player.get("id", 0)) == int(user_id) for player in players):
            return "already_joined"
        if len(players) >= cls.MAX_PLAYERS:
            raise ValueError("table_full")
        players.append({"id": int(user_id), "name": str(user_name)[:80]})
        return "joined"

    @classmethod
    def start(cls, state: dict[str, Any], *, rng: random.Random | None = None) -> None:
        players = list(state.get("players", []))
        if len(players) < 2:
            raise ValueError("minimum_players")
        if len(players) > cls.MAX_PLAYERS:
            raise ValueError("table_full")
        randomizer = rng or random.SystemRandom()
        deck = [(rank, suit) for suit in cls.SUITS for rank in cls.RANKS]
        randomizer.shuffle(deck)
        player_ids = [int(player["id"]) for player in players]
        cards_per_player = len(deck) // len(player_ids)
        hands = {
            str(player_id): [list(card) for card in deck[index * cards_per_player:(index + 1) * cards_per_player]]
            for index, player_id in enumerate(player_ids)
        }
        for hand in hands.values():
            hand.sort(key=lambda card: (cls.SUITS.index(card[1]), cls.VALUES[card[0]]))
        state.update({
            "game_id": uuid4().hex,
            "phase": "playing",
            "trump": randomizer.choice(cls.SUITS),
            "hands": hands,
            "current_turn": player_ids[0],
            "trick": [],
            "tricks_won": {str(player_id): 0 for player_id in player_ids},
            "completed_tricks": 0,
        })

    @classmethod
    def play(cls, state: dict[str, Any], user_id: int, card_index: int) -> dict[str, Any]:
        if state.get("phase") != "playing":
            raise ValueError("game_not_playing")
        user_id = int(user_id)
        if int(state.get("current_turn", 0)) != user_id:
            raise ValueError("not_your_turn")
        hand = state.get("hands", {}).get(str(user_id), [])
        try:
            card = list(hand[int(card_index)])
        except (IndexError, TypeError, ValueError):
            raise ValueError("invalid_card") from None
        trick = state.setdefault("trick", [])
        lead_suit = trick[0]["card"][1] if trick else None
        if lead_suit and card[1] != lead_suit and any(item[1] == lead_suit for item in hand):
            raise ValueError("must_follow_suit")
        hand.pop(int(card_index))
        trick.append({"player_id": user_id, "card": card})
        player_ids = [int(player["id"]) for player in state.get("players", [])]
        if len(trick) < len(player_ids):
            next_index = (player_ids.index(user_id) + 1) % len(player_ids)
            state["current_turn"] = player_ids[next_index]
            return {"phase": "playing", "trick_complete": False}

        winner = cls._trick_winner(trick, str(state["trump"]))
        state["tricks_won"][str(winner)] = int(state["tricks_won"].get(str(winner), 0)) + 1
        state["completed_tricks"] = int(state.get("completed_tricks", 0)) + 1
        state["trick"] = []
        if not any(state.get("hands", {}).values()):
            state["phase"] = "completed"
            state["scores"] = {
                key: int(value) * 10 for key, value in state.get("tricks_won", {}).items()
            }
            state["winner_id"] = max(state["scores"], key=state["scores"].get)
            return {"phase": "completed", "trick_complete": True, "winner_id": int(state["winner_id"])}
        state["current_turn"] = winner
        return {"phase": "playing", "trick_complete": True, "winner_id": winner}

    @classmethod
    def _trick_winner(cls, trick: list[dict[str, Any]], trump: str) -> int:
        lead_suit = trick[0]["card"][1]

        def strength(item: dict[str, Any]) -> tuple[int, int]:
            rank, suit = item["card"]
            if suit == trump:
                return (2, cls.VALUES[rank])
            if suit == lead_suit:
                return (1, cls.VALUES[rank])
            return (0, cls.VALUES[rank])

        return int(max(trick, key=strength)["player_id"])

    @classmethod
    def render(cls, state: dict[str, Any], viewer_id: int) -> str:
        players = state.get("players", [])
        names = ", ".join(str(player.get("name", "Oyuncu")) for player in players)
        phase = state.get("phase")
        if phase == "lobby":
            return (
                "🃏 <b>Batak masası</b>\n\n"
                f"Oyuncular: <b>{names}</b> ({len(players)}/{cls.MAX_PLAYERS})\n"
                "Katılmak için butona, masa dolunca başlatmak için başlat butonuna basın."
            )
        if phase == "completed":
            scores = state.get("scores", {})
            lines = ["🏆 <b>Batak eli tamamlandı</b>", ""]
            for player in players:
                player_id = str(player["id"])
                lines.append(f"• {player.get('name', 'Oyuncu')}: <b>{int(scores.get(player_id, 0))}</b> puan")
            return "\n".join(lines)
        current = int(state.get("current_turn", 0))
        current_name = next((p.get("name", "Oyuncu") for p in players if int(p["id"]) == current), "Oyuncu")
        trick = state.get("trick", [])
        table = " ".join(f"{item['player_id']}: {item['card'][0]}{item['card'][1]}" for item in trick) or "boş"
        hand = state.get("hands", {}).get(str(int(viewer_id)), [])
        hand_text = " ".join(f"{index + 1}:{card[0]}{card[1]}" for index, card in enumerate(hand)) or "el tamamlandı"
        return (
            "🃏 <b>Batak</b>\n\n"
            f"Koz: <b>{state.get('trump')}</b> · Sıra: <b>{current_name}</b>\n"
            f"Masadaki kartlar: {table}\n"
            f"Senin elin: {hand_text}"
        )
