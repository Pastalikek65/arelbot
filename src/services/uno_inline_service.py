"""Scoped Telegram inline controller for the durable UNO game.

The reference UNO bot's inline flow is described in
``uno-upstream-maildian/bot.py`` (AGPL-3.0). This controller uses Arel's
persistent UnoService instead of the reference bot's process-local manager.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any

from src.services.uno_service import UnoError, UnoService
from src.services.uno_ui import render_uno_text, uno_keyboard


_ROUND = re.compile(r"[0-9a-f]{16}\Z")
_BASE36 = re.compile(r"-?[0-9a-z]+\Z")
_ACTION = re.compile(r"(?:p[1-9a-z][0-9a-z]?|d|s|b|n[0-9a-z]{0,2}|c[rygb])\Z")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(value: int) -> str:
    number = int(value)
    if number < 0:
        return "-" + _base36(-number)
    if number == 0:
        return "0"
    digits = ""
    while number:
        number, digit = divmod(number, 36)
        digits = _ALPHABET[digit] + digits
    return digits


def _decode36(value: str) -> int:
    if not _BASE36.fullmatch(value) or value in {"-0", "-"}:
        raise ValueError("UNO_INLINE_SCOPE_INVALID")
    number = int(value, 36)
    if _base36(number) != value:
        raise ValueError("UNO_INLINE_SCOPE_INVALID")
    return number


class UnoInlineService:
    """Render one actor's hand and accept only signed, current choices."""

    def __init__(self, uno_service: UnoService, *, secret: str | bytes, file_ids: dict[str, str]) -> None:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret or b"")
        if len(key) < 32:
            raise ValueError("UNO_INLINE_SECRET_REQUIRED")
        self.uno = uno_service
        self.secret = key
        self.file_ids = dict(file_ids)

    def _sign(self, purpose: str, payload: str) -> str:
        digest = hmac.new(self.secret, f"uno-inline:{purpose}:{payload}".encode(), hashlib.sha256).digest()[:9]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _valid(self, purpose: str, payload: str, signature: str) -> bool:
        return hmac.compare_digest(self._sign(purpose, payload), signature)

    def create_query(self, state: dict[str, Any]) -> str:
        from src.services.uno_inline_scope import create_query
        return create_query(state, self.secret)

    def _parse_query(self, text: str) -> tuple[int, str] | None:
        parts = str(text or "").split(" ", 1)
        if len(parts) != 2 or parts[0] != "uno":
            return None
        fields = parts[1].split(".")
        if len(fields) != 3 or not _ROUND.fullmatch(fields[1]):
            return None
        try:
            chat_id = _decode36(fields[0])
        except ValueError:
            return None
        if chat_id == 0 or not self._valid("query", ".".join(fields[:2]), fields[2]):
            return None
        return chat_id, fields[1]

    @staticmethod
    def _actor(state: dict[str, Any], actor_id: int) -> dict[str, Any] | None:
        return next((player for player in state.get("players", [])
                     if int(player.get("user_id", 0)) == actor_id), None)

    def _encode_choice(self, state: dict[str, Any], actor_id: int, action: str, value: Any) -> str:
        action = str(action)
        token = "n"
        if action == "noop":
            hand = (self._actor(state, actor_id) or {}).get("hand", [])
            position = next((index for index, card in enumerate(hand, 1)
                             if str(card.get("id")) == str(value)), None)
            token = "n" + (_base36(position) if position else
                           {"info": "0", "notice": "00"}.get(str(value), ""))
        if action == "play":
            hand = (self._actor(state, actor_id) or {}).get("hand", [])
            position = next((index for index, card in enumerate(hand, 1)
                             if str(card.get("id")) == str(value)), None)
            if position is not None:
                token = "p" + _base36(position)
        elif action in {"draw", "pass", "bluff"}:
            token = {"draw": "d", "pass": "s", "bluff": "b"}[action]
        elif action == "color" and str(value) in {"red", "yellow", "green", "blue"}:
            token = "c" + {"red": "r", "yellow": "y", "green": "g", "blue": "b"}[str(value)]
        payload = ".".join(("u", _base36(int(state["chat_id"])), str(state["round_id"]),
                            _base36(int(state["revision"])), _base36(actor_id), token))
        result_id = f"{payload}.{self._sign('choice', payload)}"
        if len(result_id.encode("utf-8")) > 64:
            raise ValueError("UNO_INLINE_RESULT_ID_TOO_LONG")
        return result_id

    def _parse_choice(self, result_id: str) -> tuple[int, str, int, int, str] | None:
        if len(str(result_id).encode("utf-8")) > 64:
            return None
        parts = str(result_id).split(".")
        if len(parts) != 7 or parts[0] != "u" or not _ROUND.fullmatch(parts[2]) or not _ACTION.fullmatch(parts[5]):
            return None
        try:
            chat_id, revision, actor_id = (_decode36(parts[1]), _decode36(parts[3]), _decode36(parts[4]))
        except ValueError:
            return None
        if chat_id == 0 or revision < 0 or actor_id <= 0:
            return None
        payload = ".".join(parts[:-1])
        if not self._valid("choice", payload, parts[-1]):
            return None
        return chat_id, parts[2], revision, actor_id, parts[5]

    def _encode_offset(self, chat_id: int, round_id: str, revision: int, actor_id: int, index: int) -> str:
        payload = ".".join((_base36(chat_id), round_id, _base36(revision), _base36(actor_id), _base36(index)))
        return f"{payload}.{self._sign('offset', payload)}"

    def _decode_offset(self, offset: str, chat_id: int, round_id: str, revision: int, actor_id: int) -> int | None:
        if not offset:
            return 0
        parts = str(offset).split(".")
        if len(parts) != 6:
            return None
        try:
            stored_chat, stored_revision, stored_actor, index = (
                _decode36(parts[0]), _decode36(parts[2]), _decode36(parts[3]), _decode36(parts[4]))
        except ValueError:
            return None
        if (stored_chat, parts[1], stored_revision, stored_actor) != (chat_id, round_id, revision, actor_id):
            return None
        if index < 0 or index > 1000 or not self._valid("offset", ".".join(parts[:-1]), parts[-1]):
            return None
        return index

    async def answer_query(self, update: Any, context: Any) -> bool:
        query = getattr(update, "inline_query", None)
        if query is None or not str(getattr(query, "query", "")).startswith("uno"):
            return False
        scope = self._parse_query(query.query)
        results: list[Any] = []
        next_offset = ""
        if scope:
            chat_id, round_id = scope
            try:
                state = await self.uno.current(chat_id)
            except UnoError:
                state = None
            actor_id = int(query.from_user.id)
            if (state and state.get("status") == "playing" and state.get("round_id") == round_id
                    and int(state.get("chat_id", 0)) == chat_id and self._actor(state, actor_id)):
                revision = int(state.get("revision", 0))
                offset = self._decode_offset(str(getattr(query, "offset", "") or ""),
                                             chat_id, round_id, revision, actor_id)
                if offset is not None:
                    from src.services.uno_inline_ui import build_inline_results

                    try:
                        results, raw_next = build_inline_results(
                            state, actor_id=actor_id, file_ids=self.file_ids,
                            encode_action=lambda action, value: self._encode_choice(state, actor_id, action, value),
                            offset=offset,
                        )
                    except (KeyError, TypeError, ValueError):
                        results, raw_next = [], ""
                    results = list(results)[:50]
                    if raw_next not in (None, ""):
                        try:
                            index = int(raw_next)
                        except (TypeError, ValueError):
                            index = -1
                        if offset < index <= 1000:
                            next_offset = self._encode_offset(chat_id, round_id, revision, actor_id, index)
        await query.answer(results=results, cache_time=0, is_personal=True, next_offset=next_offset)
        return True

    async def chosen_result(self, update: Any, context: Any) -> bool:
        choice = getattr(update, "chosen_inline_result", None)
        if choice is None:
            return False
        parsed = self._parse_choice(getattr(choice, "result_id", ""))
        if parsed is None:
            return False
        chat_id, round_id, revision, actor_id, action_token = parsed
        if int(choice.from_user.id) != actor_id or self._parse_query(getattr(choice, "query", "")) != (chat_id, round_id):
            return True
        if action_token.startswith("n"):
            return True
        async with self.uno.presentation_lock(chat_id):
            try:
                state = await self.uno.current(chat_id)
                if (not state or state.get("status") != "playing" or state.get("round_id") != round_id
                        or int(state.get("chat_id", 0)) != chat_id or int(state.get("revision", -1)) != revision
                        or not self._actor(state, actor_id) or int(state.get("current_player_id", 0)) != actor_id):
                    return True
                kwargs: dict[str, Any] = {}
                if action_token.startswith("p"):
                    action = "playpos"
                    kwargs["card_position"] = _decode36(action_token[1:])
                elif action_token.startswith("c"):
                    action = "color"
                    kwargs["color"] = {"r": "red", "y": "yellow", "g": "green", "b": "blue"}[action_token[1:]]
                else:
                    action = {"d": "draw", "s": "skip", "b": "call_bluff"}[action_token]
                state = await self.uno.action(chat_id=chat_id, user_id=actor_id, action=action,
                                              action_id=f"inline:{choice.result_id}",
                                              expected_revision=revision, **kwargs)
            except (UnoError, ValueError):
                return True

            prompt_id = int(state.get("prompt_message_id", 0) or 0)
            if prompt_id and getattr(context, "bot", None):
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=prompt_id, text=render_uno_text(state),
                        reply_markup=uno_keyboard(state), parse_mode="HTML",
                    )
                except Exception as error:
                    if "not modified" not in str(error).casefold():
                        await self.uno.defer_delivery(chat_id)
                        return True
                await self.uno.mark_panel_delivered(chat_id, int(state["revision"]), prompt_id)
            else:
                await self.uno.defer_delivery(chat_id)
        return True


__all__ = ["UnoInlineService"]
