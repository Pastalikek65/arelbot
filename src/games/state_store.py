"""Persistence helpers for interactive game state."""

from __future__ import annotations

GAME_KEYS = {
    "hangman": "hangman",
    "word_game": "word_game",
    "word_guess": "word_guess",
    "batak_players": "batak",
    "bottle_players": "bottle",
    "quiz_answer": "quiz",
}


async def hydrate_games(database, chat_id: int, chat_data) -> None:
    if getattr(database, "reachable", None) is False:
        return
    for key, game_type in GAME_KEYS.items():
        try:
            state = await database.get_game_state(chat_id, game_type)
        except Exception:
            continue
        if state is not None and key not in chat_data:
            if game_type == "quiz":
                chat_data.setdefault("quiz_answer", state.get("answer", ""))
                chat_data.setdefault("quiz_question", state.get("question", ""))
            elif game_type == "batak":
                chat_data["batak_players"] = list(state.get("players", []))
                chat_data["batak_names"] = list(state.get("names", []))
            elif game_type == "bottle":
                chat_data["bottle_players"] = list(state.get("players", []))
            else:
                chat_data[key] = state


async def persist_game(database, chat_id: int, game_type: str, state) -> None:
    try:
        await database.save_game_state(chat_id, game_type, state)
    except Exception:
        return


async def delete_game(database, chat_id: int, game_type: str) -> None:
    try:
        await database.delete_game_state(chat_id, game_type)
    except Exception:
        return
