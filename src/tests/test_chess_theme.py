"""Chess tiles keep square/color identity and degrade to playable Unicode."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.games.chess import new_chess_state
from src.handlers.callback_handler import CallbackHandler
from src.services.chess_ui import chess_keyboard
from src.telegram.button_theme import decorate_markup
from src.telegram.chess_theme import CHESS_TILES
from src.tests.test_chess_surface import DataPort, Query, update


def state_for(turn="white"):
    state = new_chess_state(owner_id=1, owner_name="Ada", chat_id=-100, thread_id=42)
    state.update(status="playing", turn=turn, prompt_message_id=77)
    state["players"][0]["color"] = "white"
    state["players"].append({"id": 2, "name": "Bora", "color": "black"})
    return state


def square(markup, name):
    return next(b for row in markup["inline_keyboard"] for b in row
                if str(b.get("callback_data", "")).endswith(":sq:" + name))


@pytest.mark.parametrize("turn", ["white", "black"])
def test_all_64_native_tiles_preserve_real_squares_after_rotation(turn):
    original = chess_keyboard(state_for(turn)).to_dict()
    saved = deepcopy(original)
    premium = decorate_markup(original, premium=True)
    assert original == saved
    board = [b for row in premium["inline_keyboard"][:8] for b in row]
    assert len(board) == 64 and len({b["callback_data"] for b in board}) == 64
    assert all(b["text"] == "\u2800" and b["icon_custom_emoji_id"] for b in board)
    assert square(premium, "a1")["icon_custom_emoji_id"] == CHESS_TILES["R"]["dark"]
    assert square(premium, "a8")["icon_custom_emoji_id"] == CHESS_TILES["r"]["light"]
    assert square(premium, "h8")["icon_custom_emoji_id"] == CHESS_TILES["r"]["dark"]
    assert square(premium, "b1")["icon_custom_emoji_id"] == CHESS_TILES["N"]["light"]
    assert square(premium, "a3")["icon_custom_emoji_id"] == CHESS_TILES["empty"]["dark"]
    assert [b["text"] for b in premium["inline_keyboard"][8]] == list("HGFEDCBA" if turn == "black" else "ABCDEFGH")
    assert decorate_markup(original, premium=False)["inline_keyboard"][:9] == original["inline_keyboard"][:9]


def test_selection_capture_and_empty_target_have_distinct_styles_without_losing_piece():
    state = state_for()
    state.update(uci_moves=["e2e4", "d7d5"], selected_square="e4", legal_targets=["e5", "d5"])
    original = chess_keyboard(state).to_dict()
    premium = decorate_markup(original, premium=True)
    for name, style in (("e4", "primary"), ("e5", "success"), ("d5", "danger")):
        assert square(premium, name)["style"] == style
        assert square(original, name)["callback_data"] == square(premium, name)["callback_data"]
    assert square(premium, "d5")["icon_custom_emoji_id"] == CHESS_TILES["p"]["light"]
    assert square(original, "d5")["text"] == "♟\ufe0e"
    assert square(original, "e5")["text"] == "•"


def test_chess_catalog_has_26_unique_verified_tiles():
    identifiers = [value for pair in CHESS_TILES.values() for value in pair.values()]
    assert len(identifiers) == len(set(identifiers)) == 26


@pytest.mark.asyncio
async def test_axis_explanation_does_not_mutate_or_claim_a_move():
    state = state_for()
    port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=port)
    query = Query()
    # The shared wrapper adopts only matching board messages; use canonical ID.
    query.message.message_id = 77
    context = SimpleNamespace(chat_data={"chess": state})
    await handler._handle_chess_callback(update(1), query, context, f"chess:{state['round_id']}:axis:a")
    assert port.state["moves"] == [] and port.state.get("selected_square") is None
    assert not query.edits
    assert query.answers and "A sütunu" in query.answers[-1][0][0]


@pytest.mark.parametrize("description", ["CUSTOM_EMOJI_INVALID", "BUTTON_TEXT_INVALID"])
@pytest.mark.asyncio
async def test_rejected_premium_chess_request_retries_once_with_full_unicode_board(description):
    import json

    from src.tests.test_button_theme_transport import bot_with_replay
    bot, request, calls = bot_with_replay([
        (400, {"ok": False, "description": description}), (200, "success")])
    markup = chess_keyboard(state_for())
    async with bot:
        await bot.send_message(-100, "Satranç", reply_markup=markup)
    assert len(calls) == 2 and request.emoji_fallbacks == 1
    first = json.loads(calls[0]["reply_markup"][0])
    fallback = json.loads(calls[1]["reply_markup"][0])
    assert square(first, "a1")["icon_custom_emoji_id"] == CHESS_TILES["R"]["dark"]
    assert square(fallback, "a1")["text"] == "♖\ufe0e"
    assert all("icon_custom_emoji_id" not in b for row in fallback["inline_keyboard"] for b in row)
    assert square(first, "a1")["callback_data"] == square(fallback, "a1")["callback_data"]
