"""Durable delivery of an Akinator question or terminal result."""

import time

from telegram.error import BadRequest

from src.games.state_store import persist_game
from src.services.akinator_ui import akinator_keyboard, render_akinator_text


async def publish_akinator_panel(bot, database, state, *, query=None):
    chat_id = int(state["chat_id"])
    message_id = int(state.get("prompt_message_id", 0) or 0)
    text = render_akinator_text(state)
    markup = akinator_keyboard(state)
    try:
        if message_id:
            try:
                if query is not None:
                    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
                else:
                    await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                text=text, reply_markup=markup, parse_mode="HTML")
            except BadRequest as exc:
                if "not modified" not in str(exc).casefold():
                    raise
        else:
            raise RuntimeError("AKINATOR_PANEL_MISSING")
    except Exception:
        if bot is None:
            raise
        try:
            sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup,
                                          parse_mode="HTML", message_thread_id=state.get("thread_id"))
            message_id = int(sent.message_id)
        except Exception:
            state["panel_dirty"] = True
            failures = int(state.get("delivery_failures", 0)) + 1
            state["delivery_failures"] = failures
            state["delivery_retry_at"] = time.time() + min(60, 5 * 2 ** min(failures - 1, 4))
            await persist_game(database, chat_id, "akinator", state)
            raise
    state["prompt_message_id"] = message_id
    state["panel_dirty"] = False
    state["delivery_failures"] = 0
    state["delivery_retry_at"] = 0
    await persist_game(database, chat_id, "akinator", state)
    return message_id
