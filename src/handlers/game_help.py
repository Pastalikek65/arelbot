"""Show public rules to the requesting player without adding group messages."""
from telegram.error import TelegramError

from src.games.help_catalog import GAME_HELP
from src.games.puzzle_catalog import PUZZLES
from src.storage.game_scope import forum_thread_id

_TITLES = {entry.key: entry.title for entry in PUZZLES}


async def show_game_help(update, context) -> bool:
    query = update.callback_query
    data = str(getattr(query, "data", "") or "")
    if not data.startswith("gh:"):
        return False
    key = data.removeprefix("gh:")
    rules = GAME_HELP.get(key)
    if rules is None:
        await query.answer("Bu oyunun kuralı bulunamadı. Oyun salonundan güncel paneli aç.", show_alert=True)
        return True
    text = f"{_TITLES[key]} · Nasıl oynanır?\n\n{rules}"
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        await query.answer(rules[:180], show_alert=True)
        return True
    if str(chat.type) not in {"group", "supergroup"}:
        # Ordinary private replies support the full rules on older clients.
        try:
            await context.bot.send_message(chat_id=chat.id, text=text, parse_mode=None,
                                           disable_notification=True)
        except TelegramError:
            await query.answer(rules[:180], show_alert=True, cache_time=30)
        else:
            await query.answer(cache_time=30)
        return True
    kwargs = {"chat_id": chat.id, "text": text,
              "disable_notification": True,
              "ephemeral_message_parameters": {
                  "receiver_user_id": update.effective_user.id,
                  "callback_query_id": query.id,
                  "replace_callback_query_message": False}}
    topic = forum_thread_id(message)
    if topic is not None:
        kwargs["message_thread_id"] = topic
    try:
        # Delivery is not guaranteed by Telegram. This carries public rules
        # only; it must never acknowledge a secret role or start a game clock.
        # Keep the raw new-API response: older PTB Message parsers need not
        # understand ephemeral message fields or its response representation.
        await context.bot.do_api_request("sendMessage", api_kwargs=kwargs)
    except TelegramError:
        # No public-message fallback, and no retry of ambiguous sends.
        await query.answer(rules[:180], show_alert=True, cache_time=30)
    else:
        await query.answer(cache_time=30)
    return True
