"""Actor-scoped inline sticker choices for the persistent card table."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any, Awaitable, Callable, Mapping

from telegram import InlineQueryResultCachedSticker

from src.games import card_table as rules
from src.services.playing_card_ui import card_key


_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_ROUND = re.compile(r"[0-9a-f]{12}\Z")
_B36 = re.compile(r"-?[0-9a-z]+\Z")
_ACTION = re.compile(r"(?:p[0-9a-z]+|d[0-9a-z]+)\Z")
_GAME_CODE = {"pisti": "p", "papaz_kimde": "k"}
_CODE_GAME = {value: key for key, value in _GAME_CODE.items()}


def _b36(value: int) -> str:
    number = int(value)
    if number < 0:
        return "-" + _b36(-number)
    if number == 0:
        return "0"
    result = ""
    while number:
        number, digit = divmod(number, 36)
        result = _DIGITS[digit] + result
    return result


def _int36(value: str) -> int:
    if not _B36.fullmatch(value or ""):
        raise ValueError("ct_inline_scope")
    number = int(value, 36)
    if _b36(number) != value:
        raise ValueError("ct_inline_scope")
    return number


class CardTableInlineService:
    """Show a player's own hand and apply a signed choice through CardTableService."""

    def __init__(self, service: Any, secret: str | bytes, file_ids: Mapping[str, str],
                 *, on_applied: Callable[[Any, dict[str, Any]], Awaitable[Any]] | None = None):
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret or b"")
        if len(key) < 32:
            raise ValueError("ct_inline_secret_required")
        self.service = service
        self.secret = key
        self.file_ids = dict(file_ids)
        self.on_applied = on_applied

    def _sign(self, purpose: str, payload: str) -> str:
        digest = hmac.new(self.secret, f"ct-inline:{purpose}:{payload}".encode(), hashlib.sha256).digest()[:9]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _valid(self, purpose: str, payload: str, signature: str) -> bool:
        return hmac.compare_digest(self._sign(purpose, payload), signature)

    def create_query(self, state: Mapping[str, Any]) -> str:
        game = _GAME_CODE[state["game_key"]]
        round_id = str(state["round_id"])
        if not _ROUND.fullmatch(round_id):
            raise ValueError("ct_inline_scope")
        payload = ".".join((game, _b36(int(state["chat_id"])),
                            _b36(int(state.get("thread_id") or 0)), round_id))
        return f"ct {payload}.{self._sign('query', payload)}"

    def _parse_query(self, text: str) -> tuple[str, int, int | None, str] | None:
        words = str(text or "").split(" ", 1)
        if len(words) != 2 or words[0] != "ct":
            return None
        fields = words[1].split(".")
        if len(fields) != 5 or fields[0] not in _CODE_GAME or not _ROUND.fullmatch(fields[3]):
            return None
        try:
            chat_id, topic = _int36(fields[1]), _int36(fields[2])
        except ValueError:
            return None
        if chat_id == 0 or topic < 0 or not self._valid("query", ".".join(fields[:4]), fields[4]):
            return None
        return _CODE_GAME[fields[0]], chat_id, topic or None, fields[3]

    @staticmethod
    def _actor(state: Mapping[str, Any], actor_id: int) -> bool:
        return any(int(player.get("id", 0)) == actor_id and player.get("active")
                   for player in state.get("players", []))

    @staticmethod
    def _receipt(state: Mapping[str, Any], actor_id: int) -> int:
        receipt = state.get("private_panels", {}).get(str(actor_id), {})
        if (actor_id not in state.get("private_recipients", []) or
                receipt.get("phase_seq") != state.get("phase_seq")):
            return 0
        return int(receipt.get("message_id") or 0)

    def _choice(self, state: Mapping[str, Any], actor_id: int, token: str, receipt_id: int) -> str:
        payload = ".".join(("c", _GAME_CODE[state["game_key"]], _b36(int(state["chat_id"])),
                            _b36(int(state.get("thread_id") or 0)), str(state["round_id"]),
                            _b36(int(state["phase_seq"])), _b36(int(state["version"])),
                            _b36(actor_id), token))
        signature = self._sign("choice", f"{payload}:{receipt_id}")
        result_id = f"{payload}.{signature}"
        if len(result_id.encode()) > 64:
            raise ValueError("ct_inline_result_id_too_long")
        return result_id

    def _parse_choice(self, result_id: str) -> tuple[str, int, int | None, str, int, int, int, str, str] | None:
        if len(str(result_id).encode()) > 64:
            return None
        fields = str(result_id).split(".")
        if (len(fields) != 10 or fields[0] != "c" or fields[1] not in _CODE_GAME or
                not _ROUND.fullmatch(fields[4]) or not _ACTION.fullmatch(fields[8])):
            return None
        try:
            chat_id, topic, phase, version, actor = (
                _int36(fields[2]), _int36(fields[3]), _int36(fields[5]),
                _int36(fields[6]), _int36(fields[7]))
        except ValueError:
            return None
        if chat_id == 0 or topic < 0 or phase < 0 or version < 0 or actor <= 0:
            return None
        return (_CODE_GAME[fields[1]], chat_id, topic or None, fields[4],
                phase, version, actor, fields[8], fields[9])

    def _offset(self, scope: tuple[str, int, int | None, str], phase: int,
                version: int, actor: int, index: int) -> str:
        game, chat, topic, round_id = scope
        payload = ".".join((_GAME_CODE[game], _b36(chat), _b36(topic or 0), round_id,
                            _b36(phase), _b36(version), _b36(actor), _b36(index)))
        return f"{payload}.{self._sign('offset', payload)}"

    def _parse_offset(self, raw: str, scope: tuple[str, int, int | None, str],
                      phase: int, version: int, actor: int) -> int | None:
        if not raw:
            return 0
        fields = str(raw).split(".")
        if len(fields) != 9:
            return None
        try:
            index = _int36(fields[7])
        except ValueError:
            return None
        if index < 0 or index > 1000 or raw != self._offset(scope, phase, version, actor, index):
            return None
        return index

    def _file(self, card: str) -> str | None:
        return self.file_ids.get(f"card:{card_key(card)}")

    def _results(self, state: Mapping[str, Any], actor_id: int, offset: int,
                 receipt_id: int) -> tuple[list[InlineQueryResultCachedSticker], int | None]:
        game = state["game_key"]
        hand = state.get("hands", {}).get(str(actor_id), [])
        if game == "pisti":
            choices = [(card, f"p{_b36(index)}") for index, card in enumerate(hand)]
        else:
            choices = []
            target = rules.draw_target(state)
            if target is not None:
                # Only the opponent's card count is consulted. Their faces never
                # enter a result, result ID, or local card lookup.
                choices.extend(("back", f"d{_b36(index)}")
                               for index in range(len(state["hands"][str(target)])))
        results = []
        for card, token in choices[offset:offset + 50]:
            file_id = self.file_ids.get("back") if card == "back" else self._file(card)
            if file_id:
                results.append(InlineQueryResultCachedSticker(
                    id=self._choice(state, actor_id, token, receipt_id),
                    sticker_file_id=file_id))
        next_index = offset + 50 if offset + 50 < len(choices) else None
        return results, next_index

    async def answer_query(self, update: Any, context: Any) -> bool:
        query = getattr(update, "inline_query", None)
        if query is None or not str(getattr(query, "query", "")).startswith("ct"):
            return False
        scope = self._parse_query(query.query)
        results: list[InlineQueryResultCachedSticker] = []
        next_offset = ""
        if scope:
            game, chat, topic, round_id = scope
            try:
                state = await self.service.current(game_key=game, chat_id=chat, thread_id=topic)
                actor = int(query.from_user.id)
                if (state and state.get("phase") == "playing" and state.get("phase_ready") and
                        state.get("round_id") == round_id and state.get("game_key") == game and
                        int(state.get("chat_id", 0)) == chat and state.get("thread_id") == topic and
                        self._actor(state, actor) and
                        int(state["players"][state["turn_index"]]["id"]) == actor):
                    receipt = self._receipt(state, actor)
                    if receipt:
                        phase, version = int(state["phase_seq"]), int(state["version"])
                        offset = self._parse_offset(str(getattr(query, "offset", "") or ""),
                                                    scope, phase, version, actor)
                        if offset is not None:
                            results, next_index = self._results(state, actor, offset, receipt)
                            if next_index is not None:
                                next_offset = self._offset(scope, phase, version, actor, next_index)
            except (ValueError, KeyError, TypeError, IndexError):
                results, next_offset = [], ""
        await query.answer(results=results[:50], cache_time=0, is_personal=True,
                           next_offset=next_offset)
        return True

    async def chosen_result(self, update: Any, context: Any) -> bool:
        choice = getattr(update, "chosen_inline_result", None)
        if choice is None:
            return False
        parsed = self._parse_choice(getattr(choice, "result_id", ""))
        if parsed is None:
            return str(getattr(choice, "result_id", "")).startswith("c.")
        game, chat, topic, round_id, phase, version, actor, token, signature = parsed
        scope = (game, chat, topic, round_id)
        if int(choice.from_user.id) != actor or self._parse_query(getattr(choice, "query", "")) != scope:
            return True
        try:
            state = await self.service.current(game_key=game, chat_id=chat, thread_id=topic)
            if (not state or state.get("phase") != "playing" or not state.get("phase_ready") or
                    state.get("round_id") != round_id or state.get("game_key") != game or
                    int(state.get("chat_id", 0)) != chat or state.get("thread_id") != topic or
                    int(state.get("phase_seq", -1)) != phase or int(state.get("version", -1)) != version or
                    not self._actor(state, actor) or
                    int(state["players"][state["turn_index"]]["id"]) != actor):
                return True
            receipt = self._receipt(state, actor)
            payload = ".".join(str(choice.result_id).split(".")[:-1])
            if not receipt or not self._valid("choice", f"{payload}:{receipt}", signature):
                return True
            if game == "pisti" and token.startswith("p"):
                action, value = "play", _int36(token[1:])
                if value < 0 or value >= len(state.get("hands", {}).get(str(actor), [])):
                    return True
            elif game == "papaz_kimde" and token.startswith("d"):
                target = rules.draw_target(state)
                value = _int36(token[1:])
                if target is None or value < 0 or value >= len(state["hands"][str(target)]):
                    return True
                action = "draw"
            else:
                return True
            status, changed = await self.service.act(
                game_key=game, chat_id=chat, thread_id=topic, user_id=actor,
                user_name=getattr(choice.from_user, "first_name", "Oyuncu"),
                action_id=f"inline:{choice.result_id}", action=action, value=value,
                round_id=round_id, phase_seq=phase, expected_version=version,
                private_message_id=receipt)
        except (ValueError, KeyError, TypeError, IndexError):
            return True
        if status == "applied" and self.on_applied is not None:
            await self.on_applied(context, changed)
        return True


__all__ = ["CardTableInlineService"]
