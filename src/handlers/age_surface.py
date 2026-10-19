"""Group photo guessing controls with actor and forum-topic boundaries."""

from src.services.guess_age_service import GuessAgeServiceError
from src.services.guess_age_ui import guess_age_number_keyboard


async def handle_age_callback(handler, update, query, context, data):
    if handler.guess_age_service is None:
        await handler._safe_query_answer(query, "Yaş tahmini şu anda kullanılamıyor.", show_alert=True)
        return
    async with handler.guess_age_service.presentation_lock(int(update.effective_chat.id)):
        await _handle_age_callback(handler, update, query, context, data)


async def _handle_age_callback(handler, update, query, context, data):
    answer = handler._safe_query_answer
    chat, user = update.effective_chat, update.effective_user
    if handler.guess_age_service is None or getattr(chat, "type", None) not in {"group", "supergroup"}:
        await answer(query, "Yaş tahmini yalnızca gruplarda oynanır.", show_alert=True)
        return
    parts = str(data).split(":")
    if len(parts) not in {4, 5, 6} or not parts[2].isdigit():
        await answer(query, "Geçersiz oyun düğmesi.", show_alert=True)
        return
    try:
        state = await handler.guess_age_service.current(int(chat.id))
        if not state or state.get("round_id") != parts[1] or int(state["round"]) != int(parts[2]) or state.get("status") != "playing" or state.get("delivery_pending"):
            await answer(query, "Bu tur kapanmış. Güncel oyunu /yas ile açabilirsin.", show_alert=True)
            return
        if state.get("thread_id") is not None and getattr(query.message, "message_thread_id", None) != state["thread_id"]:
            await answer(query, "Bu oyun başka bir forum konusuna ait.", show_alert=True)
            return
        action = parts[3]
        if action == "band" and len(parts) == 5:
            if str(user.id) in state.get("answers", {}):
                await answer(query, "Bu turdaki tahminin kaydedildi.", show_alert=True)
                return
            low = int(parts[4])
            markup = guess_age_number_keyboard(state, user_id=int(user.id), low=low)
            prompt = await query.message.reply_text(f"🎂 {str(user.first_name or 'Oyuncu')[:32]} · {low}–{low + 19} aralığından tam yaşı seç.", reply_markup=markup)
            previous = await handler.guess_age_service.set_choice_prompt(int(chat.id), int(user.id), int(prompt.message_id), round_id=state["round_id"], round_no=int(state["round"]))
            if previous:
                try:
                    await context.bot.delete_message(chat_id=int(chat.id), message_id=int(previous))
                except Exception:
                    pass
            await answer(query, "Tam yaşı seç.")
            return
        if action == "dismiss" and len(parts) == 5 and parts[4] == str(user.id):
            await query.message.delete()
            await answer(query)
            return
        if action == "stop" and len(parts) == 4:
            if int(state["owner_id"]) != int(user.id) and not await handler._is_group_admin(update, context):
                await answer(query, "Oyunu başlatan kişi veya grup yöneticisi bitirebilir.", show_alert=True)
                return
            await handler.guess_age_service.cancel(int(chat.id))
            await query.edit_message_caption(caption="🎂 <b>Yaş Tahmini</b>\n\nMaç kapatıldı.", parse_mode="HTML", reply_markup=None)
            await answer(query, "Maç kapatıldı.")
            return
        if action != "pick" or len(parts) != 6 or parts[4] != str(user.id):
            await answer(query, "Bu seçim başka bir oyuncuya ait.", show_alert=True)
            return
        claimed, _ = await handler._claim_game_action(update, query, "guess_age", data)
        if not claimed:
            return
        await handler.guess_age_service.submit(chat_id=int(chat.id), user_id=int(user.id),
            display_name=str(user.first_name or "Oyuncu"), guess=parts[5], expected_round_id=parts[1], expected_round=int(parts[2]))
        await answer(query, f"{int(parts[5])} yaş tahminin kaydedildi. Puan tur sonunda açıklanacak.")
        try:
            await query.message.delete()
        except Exception:
            pass
    except (ValueError, GuessAgeServiceError) as error:
        code = getattr(error, "code", str(error))
        message = {"guess_age_already_answered": "Bu turdaki tahminin zaten kaydedildi.",
                   "GUESS_AGE_ROUND_EXPIRED": "Turun süresi doldu. Sonuçlar hazırlanıyor.",
                   "GUESS_AGE_ROUND_PREPARING": "Sonraki fotoğraf hazırlanıyor.",
                   "guess_age_out_of_range": "1 ile 120 arasında bir yaş seç."}.get(code, "Tahmin kaydedilemedi. Güncel oyunu /yas ile açabilirsin.")
        await answer(query, message, show_alert=True)
