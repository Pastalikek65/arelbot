"""Deterministic Turkish word-chain rules, independent of Telegram and storage."""

from __future__ import annotations

from copy import deepcopy
import time
import uuid

from .turkish_words import WORDS, canonical_word


LEXICON = frozenset(word for word in WORDS if 3 <= len(word) <= 24)
TURN_SECONDS = 30
ROUNDS_PER_PLAYER = 5
MAX_PASSES = 2
MAX_PLAYERS = 20


def _word(value: object) -> str:
    word = canonical_word(value)
    if len(word) < 3 or len(word) > 24 or not word.isalpha():
        raise ValueError("word_chain_invalid_word")
    if word not in LEXICON:
        raise ValueError("word_chain_unknown_word")
    return word


def _next_options(last_word: str, used: set[str]) -> bool:
    return any(word not in used and word[0] == last_word[-1] for word in LEXICON)


def new_lobby(*, chat_id: int, thread_id: int | None, owner_id: int, owner_name: str) -> dict:
    if not owner_id:
        raise ValueError("word_chain_invalid_owner")
    return {
        "round_id": uuid.uuid4().hex[:12], "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id), "players": [{"id": int(owner_id), "name": str(owner_name)[:60]}],
        "schema_version": 2, "created_at": time.time(),
        "phase": "lobby", "last_word": None, "used_words": [], "scores": {str(owner_id): 0},
        "turn_index": 0, "turn_count": 0, "turn_expires_at": None,
        "max_turns": 0, "passes": {str(owner_id): MAX_PASSES},
        "accepted_count": 0, "chain_breaks": 0,
        "winner_ids": [], "version": 0, "processed_actions": [], "message_watermark": 0,
    }


def join(state: dict, *, user_id: int, user_name: str) -> dict:
    if state.get("phase") != "lobby":
        raise ValueError("word_chain_lobby_closed")
    if any(int(player["id"]) == int(user_id) for player in state["players"]):
        raise ValueError("word_chain_already_joined")
    if len(state["players"]) >= MAX_PLAYERS:
        raise ValueError("word_chain_lobby_full")
    result = deepcopy(state)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:60]})
    result["scores"][str(user_id)] = 0
    result.setdefault("passes", {})[str(user_id)] = MAX_PASSES
    result["version"] += 1
    return result


def leave(state: dict, *, user_id: int) -> dict:
    if state.get("phase") != "lobby":
        raise ValueError("word_chain_lobby_closed")
    if not any(int(player["id"]) == int(user_id) for player in state["players"]):
        raise ValueError("word_chain_not_member")
    result = deepcopy(state)
    result["players"] = [player for player in result["players"] if int(player["id"]) != int(user_id)]
    result["scores"].pop(str(user_id), None)
    result.setdefault("passes", {}).pop(str(user_id), None)
    if not result["players"]:
        result["phase"] = "cancelled"
    elif int(result["owner_id"]) == int(user_id):
        result["owner_id"] = int(result["players"][0]["id"])
    result["version"] += 1
    return result


def start(state: dict, *, user_id: int, now: float | None = None) -> dict:
    if state.get("phase") != "lobby":
        raise ValueError("word_chain_lobby_closed")
    if int(state["owner_id"]) != int(user_id):
        raise ValueError("word_chain_not_owner")
    if len(state["players"]) < 2:
        raise ValueError("word_chain_need_players")
    result = deepcopy(state)
    result["phase"] = "playing"
    result["max_turns"] = len(result["players"]) * ROUNDS_PER_PLAYER
    result["turn_started_at"] = time.time() if now is None else float(now)
    result["turn_expires_at"] = result["turn_started_at"] + TURN_SECONDS
    result["version"] += 1
    return result


def _advance(result: dict, moment: float) -> dict:
    result["turn_count"] += 1
    result["turn_index"] = (int(result["turn_index"]) + 1) % len(result["players"])
    result["turn_expires_at"] = moment + TURN_SECONDS
    result["turn_started_at"] = moment
    if result["turn_count"] >= result.get("max_turns", len(result["players"]) * ROUNDS_PER_PLAYER):
        result["phase"] = "finished"
        result["turn_expires_at"] = None
        best = max(result["scores"].values())
        result["winner_ids"] = [int(uid) for uid, score in result["scores"].items() if score == best] if best > 0 else []
        result["score_pending"] = bool(result.get("accepted_count", 0))
    result["version"] += 1
    return result


def submit(state: dict, *, user_id: int, text: str, now: float | None = None) -> dict:
    if state.get("phase") != "playing":
        raise ValueError("word_chain_not_playing")
    players = state["players"]
    if int(players[int(state["turn_index"])]["id"]) != int(user_id):
        raise ValueError("word_chain_not_your_turn")
    moment = time.time() if now is None else float(now)
    if moment >= float(state["turn_expires_at"]):
        raise ValueError("word_chain_turn_expired")
    word = _word(text)
    used = set(state["used_words"])
    if word in used:
        raise ValueError("word_chain_repeated_word")
    last = state.get("last_word")
    if last and word[0] != last[-1]:
        raise ValueError("word_chain_wrong_letter")
    result = deepcopy(state)
    result["last_word"] = word
    result["used_words"].append(word)
    result["scores"][str(user_id)] += len(word)
    result["accepted_count"] = int(result.get("accepted_count", 0)) + 1
    if not _next_options(word, set(result["used_words"])):
        result["last_word"] = None
        result["chain_breaks"] = int(result.get("chain_breaks", 0)) + 1
    return _advance(result, moment)


def pass_turn(state: dict, *, user_id: int, now: float | None = None) -> dict:
    if state.get("phase") != "playing":
        raise ValueError("word_chain_not_playing")
    if int(state["players"][int(state["turn_index"])]["id"]) != int(user_id):
        raise ValueError("word_chain_not_your_turn")
    moment = time.time() if now is None else float(now)
    if moment >= float(state["turn_expires_at"]):
        raise ValueError("word_chain_turn_expired")
    remaining = int(state.get("passes", {}).get(str(user_id), MAX_PASSES))
    if remaining <= 0:
        raise ValueError("word_chain_no_passes")
    result = deepcopy(state)
    result.setdefault("passes", {})[str(user_id)] = remaining - 1
    return _advance(result, moment)


def expire_turn(state: dict, *, now: float | None = None) -> dict:
    if state.get("phase") != "playing":
        raise ValueError("word_chain_not_playing")
    moment = time.time() if now is None else float(now)
    if moment < float(state["turn_expires_at"]):
        raise ValueError("word_chain_turn_not_expired")
    return _advance(deepcopy(state), moment)


def stop(state: dict, *, user_id: int, moderator: bool = False) -> dict:
    if state.get("phase") not in {"lobby", "playing"}:
        raise ValueError("word_chain_already_ended")
    if int(state["owner_id"]) != int(user_id) and not moderator:
        raise ValueError("word_chain_not_owner")
    result = deepcopy(state)
    result["phase"] = "cancelled"
    result["turn_expires_at"] = None
    result["version"] += 1
    return result
