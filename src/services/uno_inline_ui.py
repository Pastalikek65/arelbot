"""Private inline UNO sticker gallery backed by maildian's result presentation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from telegram import InlineQueryResultArticle, InputTextMessageContent

from src.games.uno import can_play as legacy_can_play
from src.vendor.uno_maildian import inline_results as upstream

MAX_RESULTS = 50
_COLOR = {"blue": "b", "green": "g", "red": "r", "yellow": "y"}
_KIND = {"draw2": "draw", "skip": "skip", "reverse": "reverse"}
_BLACK = {"wild": "colorchooser", "draw4": "draw_four"}


def card_asset_key(card: Mapping[str, Any]) -> str:
    """Translate Arel's JSON card to the exact upstream PNG/sticker key."""
    color, kind = str(card.get("color")), str(card.get("kind"))
    if color == "black" and kind in _BLACK:
        return _BLACK[kind]
    if color in _COLOR and (kind in _KIND or kind in set("0123456789")):
        return f"{_COLOR[color]}_{_KIND.get(kind, kind)}"
    raise ValueError("uno_inline_invalid_card")


def _can_play(state: Mapping[str, Any], card: Mapping[str, Any], actor_id: int) -> bool:
    if state.get("engine") == "maildian":
        # The core adapter may be absent in isolated UI tests, or unavailable
        # during an incremental rollout. Preserve the legacy legality path.
        try:
            from src.games.uno_upstream import can_play as upstream_can_play
        except ImportError:
            pass
        else:
            return bool(upstream_can_play(state, card, player_id=actor_id))
    return bool(legacy_can_play(state, card, player_id=actor_id))


def _notice(encode_action: Callable, title: str, message: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=encode_action("noop", "notice"), title=title,
        input_message_content=InputTextMessageContent(message),
    )


def build_inline_results(
    state: Mapping[str, Any], *, actor_id: int, file_ids: Mapping[str, str],
    encode_action: Callable[[str, str | None], str], offset: int = 0,
) -> tuple[list, str]:
    """Return at most 50 results from the actor's own hand and a raw next offset.

    The inline controller binds actor/chat/round/revision to encoded action IDs
    and signs the page offset. This renderer never reads another player's cards
    when choosing card result images or legality.
    """
    actor = next((p for p in state.get("players", [])
                  if int(p.get("user_id", 0)) == int(actor_id)), None)
    if actor is None:
        return [_notice(encode_action, "Bu oyunda değilsin", "Grup oyununa katılıp yeniden dene.")], ""
    if state.get("status") != "playing":
        return [_notice(encode_action, "Oyun başlamadı", "Kurucunun oyunu başlatmasını bekle.")], ""

    current = int(state.get("current_player_id", 0) or 0) == int(actor_id)
    choosing_color = bool(state.get("awaiting_color"))
    drawn_id = str(actor.get("drawn_card_id") or "")
    hand = actor.get("hand") or []
    results: list = []

    if current:
        if choosing_color:
            upstream.add_choose_color(results, state, encode_action)
        elif drawn_id or actor.get("draw_attempted"):
            upstream.add_pass(results, state, file_ids, encode_action)
        else:
            upstream.add_draw(results, state, file_ids, encode_action)
        top = (state.get("discard") or [{}])[-1]
        if not choosing_color and int(state.get("pending_draw", 0) or 0) == 4 and top.get("kind") == "draw4":
            upstream.add_call_bluff(results, state, file_ids, encode_action)

    for card in hand:
        key = card_asset_key(card)
        playable = bool(current and not choosing_color
                        and (not drawn_id or str(card.get("id")) == drawn_id)
                        and _can_play(state, card, actor_id))
        upstream.add_card(results, state, card, key, playable, file_ids, encode_action)

    if current:
        upstream.add_gameinfo(results, state, file_ids, encode_action)
    if not results:
        return [_notice(encode_action, "Elin boş", "Oyun durumunu gruptaki panelden izle.")], ""

    index = int(offset)
    if index < 0 or index >= len(results):
        raise ValueError("uno_inline_invalid_offset")
    page = results[index:index + MAX_RESULTS]
    next_index = index + len(page)
    return page, (str(next_index) if next_index < len(results) else "")


__all__ = ["MAX_RESULTS", "build_inline_results", "card_asset_key"]
