"""Keep the active UNO controls near the latest group message on turn changes."""

from telegram.error import BadRequest

from src.services.uno_ui import render_uno_text, uno_keyboard


def needs_new_turn_panel(state):
    return (state.get("status") == "playing"
            and bool(state.get("prompt_message_id"))
            and state.get("panel_turn_player_id") != state.get("current_player_id"))


async def publish_uno_panel(bot, service, chat_id, state, *, query=None):
    """Send one fresh panel per player turn; edit within the same turn."""
    kwargs = {"chat_id": chat_id, "text": render_uno_text(state),
              "reply_markup": uno_keyboard(state), "parse_mode": "HTML"}
    old_id = int(state.get("prompt_message_id", 0) or 0)
    revision = int(state.get("revision", 0))
    new_panel = needs_new_turn_panel(state) and bot is not None

    if new_panel or not old_id:
        if bot is None:
            raise RuntimeError("UNO_PANEL_BOT_UNAVAILABLE")
        sent = await bot.send_message(**kwargs, message_thread_id=state.get("thread_id"))
        panel_id = int(sent.message_id)
        await service.set_prompt_message(chat_id=chat_id, message_id=panel_id,
                                         expected_revision=revision)
    else:
        panel_id = old_id
        try:
            if query is not None:
                await query.edit_message_text(kwargs["text"], reply_markup=kwargs["reply_markup"],
                                              parse_mode="HTML")
            else:
                await bot.edit_message_text(message_id=old_id, **kwargs)
        except BadRequest as error:
            message = str(error).casefold()
            if "not modified" in message:
                pass
            elif bot is not None and any(code in message for code in ("not found", "can't be edited", "cannot be edited")):
                sent = await bot.send_message(**kwargs, message_thread_id=state.get("thread_id"))
                panel_id = int(sent.message_id)
                await service.set_prompt_message(chat_id=chat_id, message_id=panel_id,
                                                 expected_revision=revision)
            else:
                raise

    await service.mark_panel_delivered(chat_id, revision, panel_id)
    if new_panel and old_id != panel_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_id,
                                                    reply_markup=None)
            except Exception:
                pass
    return panel_id
