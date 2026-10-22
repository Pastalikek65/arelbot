"""Private inline gallery for the standard 52-card Kenney deck.

The caller supplies a signed action encoder and the actor's authoritative
game state. This module only renders that actor's hand; legality stays in the
game service and is passed as playable card indices.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
import json
from pathlib import Path
from typing import Any

from telegram import InlineQueryResultArticle, InlineQueryResultCachedSticker
from telegram import InputTextMessageContent


MAX_RESULTS = 50
_RANKS = frozenset({"2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"})
_SUITS = {"♠": "spades", "♥": "hearts", "♦": "diamonds", "♣": "clubs",
          "S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}
_ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets" / "playing_cards"


def card_key(card: tuple[str, str] | list[str] | Mapping[str, Any] | str) -> str:
    """Map Batak pairs, explicit card dicts, or Pişti's ``AS`` form to art."""
    if isinstance(card, str):
        card = card.replace("\ufe0f", "")
        rank, suit = card[:-1], card[-1:]
    elif isinstance(card, Mapping):
        rank, suit = str(card.get("rank", "")), str(card.get("suit", ""))
    elif isinstance(card, (tuple, list)) and len(card) == 2:
        rank, suit = str(card[0]), str(card[1])
    else:
        raise ValueError("playing_card_invalid")
    rank = rank.upper()
    suit = suit.replace("\ufe0f", "")
    if rank not in _RANKS or suit not in _SUITS:
        raise ValueError("playing_card_invalid")
    return f"{_SUITS[suit]}_{rank}"


def load_sticker_ids(path: str | Path | None = None) -> dict[str, str]:
    """Load a complete bot-specific 105-sticker registry, or {} until provisioned."""
    location = Path(path) if path is not None else _ASSET_ROOT / "telegram_stickers.json"
    if not location.exists():
        return {}
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
        ids = payload.get("file_ids", payload)
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError("playing_card_invalid_registry") from exc
    expected = {f"card:{suit}_{rank}" for suit in ("spades", "hearts", "diamonds", "clubs")
                for rank in _RANKS}
    expected |= {key.replace("card:", "grey:", 1) for key in expected}
    expected.add("back")
    if (not isinstance(ids, dict) or set(ids) != expected
            or any(not isinstance(value, str) or not value.strip() for value in ids.values())):
        raise ValueError("playing_card_incomplete_registry")
    return dict(ids)


def _current_player_id(state: Mapping[str, Any]) -> int | None:
    direct = state.get("current_turn", state.get("current_player_id"))
    if direct is not None:
        return int(direct)
    players = state.get("players") or []
    turn_index = state.get("turn_index")
    if isinstance(turn_index, int) and 0 <= turn_index < len(players):
        return int(players[turn_index]["id"])
    return None


def _page(results: list, offset: int) -> tuple[list, str]:
    index = int(offset)
    if index < 0 or index >= len(results):
        raise ValueError("playing_card_invalid_offset")
    page = results[index:index + MAX_RESULTS]
    next_index = index + len(page)
    return page, str(next_index) if next_index < len(results) else ""


def render_cards_inline(
    state: Mapping[str, Any], *, actor_id: int, file_ids: Mapping[str, str],
    encode_action: Callable[[str, str | None], str],
    playable_indices: Collection[int] = (), offset: int = 0,
) -> tuple[list, str]:
    """Render only ``hands[actor_id]``; the service supplies legal indices.

    ``encode_action`` must bind actor, game/round and revision before a
    selected inline result is accepted. A grey/nonplayable card encodes noop.
    """
    actor_id = int(actor_id)
    player = next((p for p in state.get("players", [])
                   if int(p.get("id", p.get("user_id", 0))) == actor_id), None)
    if player is None:
        results = [InlineQueryResultArticle(
            id=encode_action("noop", "notice"), title="Bu oyunda değilsin",
            input_message_content=InputTextMessageContent("Grup oyununa katılıp yeniden dene."),
        )]
        return _page(results, offset)
    if state.get("phase") != "playing":
        results = [InlineQueryResultArticle(
            id=encode_action("noop", "notice"), title="Oyun başlamadı",
            input_message_content=InputTextMessageContent("Oyunun başlamasını bekle."),
        )]
        return _page(results, offset)
    hand = state.get("hands", {}).get(str(actor_id), [])
    if not isinstance(hand, list):
        raise ValueError("playing_card_invalid_hand")
    can_act = _current_player_id(state) == actor_id
    legal = set(playable_indices) if can_act else set()
    if any(not isinstance(index, int) or index < 0 or index >= len(hand) for index in legal):
        raise ValueError("playing_card_invalid_indices")
    results = []
    for index, card in enumerate(hand):
        key = card_key(card)
        playable = index in legal
        results.append(InlineQueryResultCachedSticker(
            id=encode_action("play" if playable else "noop", str(index)),
            sticker_file_id=(file_ids[f"card:{key}"] if playable
                             else file_ids.get(f"grey:{key}", file_ids[f"card:{key}"])),
            **({} if playable else {"input_message_content": InputTextMessageContent(
                "Bu kart şu anda oynanamaz. Oyun durumunu grupta kontrol et.")}),
        ))
    if not results:
        results = [InlineQueryResultArticle(
            id=encode_action("noop", "empty"), title="Elinde kart yok",
            input_message_content=InputTextMessageContent("Oyun durumunu grupta kontrol et."),
        )]
    return _page(results, offset)


def render_card_backs_inline(
    count: int, *, file_ids: Mapping[str, str],
    encode_action: Callable[[str, str | None], str], offset: int = 0,
    action: str = "noop",
) -> tuple[list, str]:
    """Represent hidden cards by position, without inspecting their faces.

    A service may opt into a game-specific ``action`` after it checks who may
    draw from the concealed hand; the default is a safe no-op.
    """
    if not isinstance(count, int) or count < 0:
        raise ValueError("playing_card_invalid_count")
    results = [InlineQueryResultCachedSticker(
        id=encode_action(action, f"back:{index}"),
        sticker_file_id=file_ids["back"],
        input_message_content=InputTextMessageContent("Kapalı kart"),
    ) for index in range(count)]
    if not results:
        results = [InlineQueryResultArticle(
            id=encode_action("noop", "empty"), title="Kapalı kart yok",
            input_message_content=InputTextMessageContent("Kapalı kart yok."),
        )]
    return _page(results, offset)


__all__ = ["MAX_RESULTS", "card_key", "load_sticker_ids", "render_card_backs_inline",
           "render_cards_inline"]
