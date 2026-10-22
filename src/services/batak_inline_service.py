"""Actor-private, signed inline card gallery for a persistent Batak round.

The group panel must have a persisted ``prompt_message_id`` before its
``create_query`` result is displayed. BatakService remains the only writer of
trick state and checks the exact turn token under its per-chat lock.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any, Callable, Mapping

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.batak_round import BatakRound
from src.services.playing_card_ui import card_key, render_cards_inline


_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHORT = re.compile(r"[0-9a-f]{12}\Z")
_ACTION = re.compile(r"[pn][1-9a-z][0-9a-z]?\Z")


def _base36(value: int) -> str:
    value = int(value)
    if value < 0:
        return "-" + _base36(-value)
    if value == 0:
        return "0"
    digits = ""
    while value:
        value, digit = divmod(value, 36)
        digits = _ALPHABET[digit] + digits
    return digits


def _decode36(value: str) -> int:
    if not re.fullmatch(r"-?[0-9a-z]+", value or "") or value in {"-", "-0"}:
        raise ValueError("bad_base36")
    number = int(value, 36)
    if _base36(number) != value:
        raise ValueError("bad_base36")
    return number


class BatakInlineService:
    def __init__(
        self, batak_service: Any, *, secret: str | bytes, file_ids: Mapping[str, str],
        render_public: Callable[[dict], str], keyboard: Callable[[dict], Any],
        defer_delivery: Callable[[int, dict], Any] | None = None,
    ) -> None:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret or b"")
        if len(key) < 32:
            raise ValueError("BATAK_INLINE_SECRET_REQUIRED")
        self.batak = batak_service
        self.secret = key
        self.file_ids = dict(file_ids)
        self.render_public = render_public
        self.keyboard = keyboard
        self.defer_delivery = defer_delivery

    def _sign(self, purpose: str, payload: str) -> str:
        digest = hmac.new(self.secret, f"batak-inline:{purpose}:{payload}".encode(), hashlib.sha256).digest()[:9]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _valid(self, purpose: str, payload: str, signature: str) -> bool:
        return hmac.compare_digest(self._sign(purpose, payload), signature)

    def create_query(self, chat_id: int, state: Mapping[str, Any]) -> str:
        game_id = str(state.get("game_id") or "")
        panel_id = int(state.get("prompt_message_id") or 0)
        if state.get("phase") != "playing" or not _ID.fullmatch(game_id) or panel_id <= 0:
            raise ValueError("BATAK_INLINE_PANEL_NOT_READY")
        payload = ".".join((_base36(chat_id), game_id, _base36(panel_id)))
        return f"batak {payload}.{self._sign('query', payload)}"

    def _parse_query(self, text: str) -> tuple[int, str, int] | None:
        parts = str(text or "").split(" ", 1)
        if len(parts) != 2 or parts[0] != "batak":
            return None
        fields = parts[1].split(".")
        if len(fields) != 4 or not _ID.fullmatch(fields[1]):
            return None
        try:
            chat_id, panel_id = _decode36(fields[0]), _decode36(fields[2])
        except ValueError:
            return None
        if chat_id == 0 or panel_id <= 0 or not self._valid("query", ".".join(fields[:3]), fields[3]):
            return None
        return chat_id, fields[1], panel_id

    def _choice(self, chat_id: int, state: Mapping[str, Any], actor_id: int, position: int, *, playable: bool) -> str:
        game_id = str(state["game_id"])
        token = BatakRound.turn_token(dict(state)).split(":")
        action = ("p" if playable else "n") + _base36(position)
        payload = ".".join(("b", _base36(chat_id), game_id[:12], _base36(int(token[1])),
                            _base36(int(token[2])), _base36(actor_id), action))
        encoded = f"{payload}.{self._sign('choice', payload)}"
        if len(encoded.encode()) > 64:
            raise ValueError("BATAK_INLINE_RESULT_ID_TOO_LONG")
        return encoded

    def _parse_choice(self, result_id: str) -> tuple[int, str, int, int, int, str] | None:
        if len(str(result_id).encode()) > 64:
            return None
        parts = str(result_id).split(".")
        if len(parts) != 8 or parts[0] != "b" or not _SHORT.fullmatch(parts[2]) or not _ACTION.fullmatch(parts[6]):
            return None
        try:
            chat_id, trick_number, table_count, actor_id = map(_decode36, (parts[1], parts[3], parts[4], parts[5]))
        except ValueError:
            return None
        if (chat_id == 0 or actor_id <= 0 or trick_number < 0 or table_count < 0 or
                not self._valid("choice", ".".join(parts[:-1]), parts[-1])):
            return None
        return chat_id, parts[2], trick_number, table_count, actor_id, parts[6]

    @staticmethod
    def _hand(state: Mapping[str, Any], actor_id: int) -> list | None:
        if actor_id not in {int(p.get("id", 0)) for p in state.get("players", [])}:
            return None
        hand = state.get("hands", {}).get(str(actor_id))
        return hand if isinstance(hand, list) else None

    @staticmethod
    def _legal(state: Mapping[str, Any], hand: list, position: int, actor_id: int) -> bool:
        if int(state.get("current_turn", 0)) != actor_id:
            return False
        card = hand[position]
        trick = state.get("trick") or []
        lead = trick[0]["card"][1] if trick else None
        return not lead or card[1] == lead or not any(other[1] == lead for other in hand)

    async def answer_query(self, update: Any, context: Any) -> bool:
        query = getattr(update, "inline_query", None)
        if query is None or not str(getattr(query, "query", "")).startswith("batak"):
            return False
        parsed = self._parse_query(query.query)
        results = []
        if parsed:
            chat_id, game_id, panel_id = parsed
            try:
                state = await self.batak.snapshot(chat_id)
            except (RuntimeError, ValueError):
                state = None
            actor_id = int(query.from_user.id)
            if (state and state.get("phase") == "playing" and state.get("game_id") == game_id
                    and int(state.get("prompt_message_id") or 0) == panel_id):
                hand = self._hand(state, actor_id)
                if hand is not None:
                    try:
                        legal = [index for index in range(len(hand)) if self._legal(state, hand, index, actor_id)]
                        results, _ = render_cards_inline(
                            state, actor_id=actor_id, file_ids=self.file_ids,
                            encode_action=lambda action, value: self._choice(
                                chat_id, state, actor_id,
                                int(value) + 1 if str(value).isdigit() else 1,
                                playable=action == "play"),
                            playable_indices=legal,
                        )
                    except (KeyError, TypeError, ValueError, IndexError):
                        results = []
        await query.answer(results=results[:50], cache_time=0, is_personal=True)
        return True

    async def chosen_result(self, update: Any, context: Any) -> bool:
        choice = getattr(update, "chosen_inline_result", None)
        if choice is None:
            return False
        parsed = self._parse_choice(getattr(choice, "result_id", ""))
        if parsed is None:
            return False
        chat_id, short_id, trick_number, table_count, actor_id, action = parsed
        query_scope = self._parse_query(getattr(choice, "query", ""))
        if (int(choice.from_user.id) != actor_id or query_scope is None or
                query_scope[0] != chat_id or query_scope[1][:12] != short_id):
            return True
        if action.startswith("n"):
            return True
        async with self.batak.presentation_lock(chat_id):
            return await self._chosen_result_locked(choice, context, parsed, query_scope)

    async def _chosen_result_locked(self, choice: Any, context: Any,
                                    parsed: tuple[int, str, int, int, int, str],
                                    query_scope: tuple[int, str, int]) -> bool:
        chat_id, short_id, trick_number, table_count, actor_id, action = parsed
        try:
            state = await self.batak.snapshot(chat_id)
            if (not state or state.get("phase") != "playing" or state.get("game_id") != query_scope[1]
                    or int(state.get("prompt_message_id") or 0) != query_scope[2]
                    or BatakRound.turn_token(state) != f"{short_id}:{trick_number}:{table_count}"
                    or self._hand(state, actor_id) is None):
                return True
            position = _decode36(action[1:]) - 1
            hand = self._hand(state, actor_id)
            if position < 0 or position >= len(hand) or not self._legal(state, hand, position, actor_id):
                return True
            result = await self.batak.play(chat_id=chat_id, user_id=actor_id, card_index=position,
                                           action_id=f"inline:{choice.result_id}",
                                           expected_turn_token=BatakRound.turn_token(state),
                                           expected_panel_message_id=query_scope[2])
        except (RuntimeError, ValueError, KeyError, IndexError, TypeError):
            return True
        fresh = result.get("state") or {}
        if not result.get("terminal"):
            try:
                current = await self.batak.snapshot(chat_id)
            except (RuntimeError, ValueError):
                current = None
            if current and current.get("game_id") == fresh.get("game_id"):
                fresh = current
        bot = getattr(context, "bot", None)
        if bot:
            try:
                markup = self.keyboard(fresh)
                if fresh.get("phase") == "playing" and int(fresh.get("prompt_message_id") or 0) == query_scope[2]:
                    inline_query = self.create_query(chat_id, fresh)
                    rows = list(markup.inline_keyboard) if markup is not None else []
                    if not any(button.switch_inline_query_current_chat == inline_query
                               for row in rows for button in row):
                        rows.insert(0, [InlineKeyboardButton(
                            "🃏 Kartlarımı aç", switch_inline_query_current_chat=inline_query)])
                    markup = InlineKeyboardMarkup(rows)
                await bot.edit_message_text(chat_id=chat_id, message_id=query_scope[2],
                                            text=self.render_public(fresh), reply_markup=markup,
                                            parse_mode="HTML")
                return True
            except Exception:
                pass
        if self.defer_delivery:
            await self.defer_delivery(chat_id, fresh)
        return True


__all__ = ["BatakInlineService", "card_key"]
