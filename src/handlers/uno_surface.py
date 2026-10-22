"""Versioned UNO Telegram controls over the durable service."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError

from src.services.uno_service import UnoError
from src.services.uno_ui import CARD_POSITION_PAGE_SIZE, render_uno_text, render_uno_hand_alert, uno_keyboard


async def handle_uno_callback(handler, update, query, context, data):
    if handler.uno_service is None:
        await handler._safe_query_answer(query, "UNO şu anda kullanılamıyor.", show_alert=True)
        return
    async with handler.uno_service.presentation_lock(int(update.effective_chat.id)):
        await _handle_uno_callback(handler, update, query, context, data)


async def _handle_uno_callback(handler, update, query, context, data):
    answer = handler._safe_query_answer
    chat, user = update.effective_chat, update.effective_user
    if handler.uno_service is None or getattr(chat, "type", None) not in {"group", "supergroup"}:
        await answer(query, "UNO yalnızca gruplarda oynanır.", show_alert=True)
        return
    parts = str(data).split(":")
    try:
        state = await handler.uno_service.current(int(chat.id))
        if not state or len(parts) < 3 or state.get("round_id") != parts[1]:
            await answer(query, "Bu tur kapanmış. Güncel masayı /uno ile açabilirsin.", show_alert=True)
            return
        message = getattr(query, "message", None)
        expected_thread = state.get("thread_id")
        if expected_thread is not None and getattr(message, "message_thread_id", None) != expected_thread:
            await answer(query, "Bu UNO düğmesi başka bir forum konusuna ait.", show_alert=True)
            return
        if len(parts) < 4 or not parts[2].isdigit():
            await answer(query, "Bu panel eski sürüme ait. /uno ile güncelle.", show_alert=True)
            return
        revision, action = int(parts[2]), parts[3]
        if action == "rules":
            text = ("Kartlarımı aç: renkli kartı seç. +2 birikir; +4 blöfüne itiraz edilebilir. Son kart joker/+4 olamaz. Tek kartta UNO de. 500 puan maçı kazandırır."
                    if state.get("engine") == "maildian" else
                    "Aynı renk veya değeri oyna. Elim düğmesi kartlarını yalnız sana gösterir. Tek kartta UNO de. +2/+4 cezaları uygulanır; üst üste eklenmez. 500 puana ulaşan maçı kazanır.")
            await answer(query, text, show_alert=True)
            return
        if action == "hand" and len(parts) == 5:
            page = int(parts[4]) if parts[4].isdigit() else -1
            await answer(query, render_uno_hand_alert(state, user_id=int(user.id), page=page), show_alert=True)
            return
        prompt_id = int(state.get("prompt_message_id", 0) or 0)
        if prompt_id and int(getattr(message, "message_id", 0) or 0) != prompt_id:
            await answer(query, "Güncel masa başka bir mesajda. /uno ile açabilirsin.", show_alert=True)
            return
        if revision != int(state.get("revision", 0)):
            await answer(query, "Masa değişti. Güncel düğmeleri kullan.", show_alert=True)
            return
        if action == "cardpage":
            if len(parts) != 5 or not parts[4].isdigit():
                await answer(query, "Geçersiz kart sayfası.", show_alert=True)
                return
            current_id = int(state.get("current_player_id", 0) or 0)
            if current_id != int(user.id):
                await answer(query, "Kart sayfasını sıradaki oyuncu değiştirebilir.", show_alert=True)
                return
            current = next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == current_id), None)
            pages = max(1, (len((current or {}).get("hand", [])) + CARD_POSITION_PAGE_SIZE - 1) // CARD_POSITION_PAGE_SIZE)
            page = int(parts[4])
            if page >= pages:
                await answer(query, "Bu kart sayfası yok. Güncel düğmeleri kullan.", show_alert=True)
                return
            try:
                await query.edit_message_text(render_uno_text(state), reply_markup=uno_keyboard(state, card_page=page), parse_mode="HTML")
            except BadRequest as error:
                if "not modified" not in str(error).casefold():
                    raise
            await answer(query)
            return
        common = {"chat_id": int(chat.id), "user_id": int(user.id), "expected_revision": revision}
        action_id = handler._game_action_id(query, int(user.id), data)
        if action == "join":
            state = await handler.uno_service.join(**common, name=str(user.first_name or "Oyuncu"))
        elif action == "start":
            state = await handler.uno_service.begin(**common, action_id=action_id)
        elif action == "next":
            state = await handler.uno_service.next_round(**common, action_id=action_id)
        elif action == "stop":
            moderator = await handler._is_group_admin(update, context)
            await handler.uno_service.cancel(**common, moderator=moderator)
            await query.edit_message_text("🛑 UNO masası kapatıldı.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🃏 Yeni UNO", callback_data="game_start_uno"), InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games")]]))
            await answer(query, "Masa kapatıldı.")
            return
        else:
            value = parts[4] if len(parts) == 5 else None
            state = await handler.uno_service.action(**common, action=action, action_id=action_id,
                card_id=value if action == "play" else None,
                card_position=int(value) if action == "playpos" and value and value.isdigit() else None,
                color=value if action == "color" else None,
                target_id=int(value) if action == "challenge" and value and value.isdigit() else None)
        try:
            await query.edit_message_text(render_uno_text(state), reply_markup=uno_keyboard(state), parse_mode="HTML")
        except BadRequest as error:
            if "not modified" not in str(error).casefold():
                raise
        await handler.uno_service.mark_panel_delivered(int(chat.id), int(state.get("revision", 0)), int(getattr(query.message, "message_id", 0) or 0))
        await answer(query)
    except UnoError as error:
        messages = {
            "UNO_STALE_ACTION": "Masa değişti. Güncel düğmeleri kullan.",
            "UNO_NOT_YOUR_TURN": "Sıra sende değil.",
            "UNO_CARD_CANNOT_PLAY": "Bu kart şu an oynanamaz.",
            "UNO_CARD_NOT_IN_HAND": "Bu kart elinde değil.",
            "UNO_DUPLICATE_ACTION": "Bu hamle zaten işlendi.",
            "UNO_CHOOSE_COLOR_FIRST": "Önce renk seç.",
            "UNO_OWNER_ONLY": "Bu işlemi masayı kuran oyuncu yapabilir.",
            "UNO_NEED_TWO_PLAYERS": "Başlamak için en az iki oyuncu gerekiyor.",
            "UNO_LOBBY_FULL": "Masa dolu; en fazla 8 oyuncu katılabilir.",
            "UNO_PLAYER_NOT_FOUND": "Bu masada oyuncu değilsin.",
            "UNO_CHALLENGE_NOT_ALLOWED": "Bu oyuncuya şu an UNO cezası verilemez.",
            "UNO_CALL_NOT_ALLOWED": "UNO demek için elinde bir veya iki kart olmalı.",
            "UNO_SCORE_PENDING": "Tur puanı kaydediliyor. Biraz sonra tekrar dene.",
            "UNO_STORAGE_UNAVAILABLE": "Oyun kaydedilemedi. Biraz sonra tekrar dene.",
        }
        await answer(query, messages.get(error.code, "İşlem uygulanamadı. Güncel masayı /uno ile açabilirsin."), show_alert=True)
    except TelegramError:
        # A page edit may fail without changing state; a move may already be
        # persisted. Recover through the canonical panel in either case.
        await answer(query, "Panel güncellenemedi. Güncel masayı /uno ile açabilirsin.", show_alert=True)
