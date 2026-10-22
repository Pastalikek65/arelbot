"""Group photo guessing controls with actor and forum-topic boundaries."""

from src.services.guess_age_service import GuessAgeServiceError
import math
import time

from src.games.guess_age import round_expired
from src.services.guess_age_ui import guess_age_number_keyboard, guess_age_band_keyboard, guess_age_keyboard, render_guess_age_text
from src.storage.game_scope import forum_thread_id
from telegram.error import BadRequest, TelegramError


async def refresh_age_caption(bot, state):
    """Update participation without another photo; caller holds the panel lock."""
    try:
        await bot.edit_message_caption(
            chat_id=int(state["chat_id"]), message_id=int(state["prompt_message_id"]),
            caption=render_guess_age_text(state), parse_mode="HTML", reply_markup=guess_age_keyboard(state),
        )
    except Exception:
        # A display failure never retracts the already committed guess. The
        # status button reads the authoritative count even if this edit fails.
        pass


async def handle_age_callback(handler, update, query, context, data):
    if str(data).startswith("guessage:sources:"):
        if getattr(update.effective_chat, "type", None) != "private":
            await handler._safe_query_answer(query, "Fotoğraf kaynaklarını botun özel sohbetinde /yas kaynaklar ile açabilirsin.", show_alert=True)
            return
        from src.services.guess_age_ui import render_age_sources
        try:
            page = int(str(data).split(":")[2])
        except (ValueError, IndexError):
            await handler._safe_query_answer(query)
            return
        text, keyboard = render_age_sources(getattr(handler.guess_age_service, "assets", []), page)
        await handler._safe_query_answer(query)
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
        return
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
    action = parts[3]
    shapes = {"help": {4}, "status": {4}, "choose": {4}, "stop": {4},
              "band": {5, 6}, "bands": {5}, "pick": {6}, "dismiss": {5}}
    if len(parts) not in shapes.get(action, set()):
        await answer(query, "Geçersiz oyun düğmesi.", show_alert=True)
        return
    try:
        state = await handler.guess_age_service.current(int(chat.id))
        if not state or state.get("round_id") != parts[1] or int(state["round"]) != int(parts[2]) or state.get("status") != "playing" or state.get("delivery_pending") or state.get("initial_delivery_pending"):
            await answer(query, "Bu tur kapanmış. Güncel oyunu /yas ile açabilirsin.", show_alert=True)
            return
        if state.get("thread_id") != forum_thread_id(query.message):
            await answer(query, "Bu oyun başka bir forum konusuna ait.", show_alert=True)
            return
        personal = (action in {"pick", "dismiss", "bands"} or (action == "band" and len(parts) == 6))
        prompt_id = int(getattr(query.message, "message_id", 0) or 0)
        if personal:
            if parts[4] != str(user.id) or prompt_id != int(state.get("choice_prompts", {}).get(str(user.id), 0)):
                await answer(query, "Bu seçim sana veya güncel fotoğrafa ait değil.", show_alert=True)
                return
        elif prompt_id != int(state.get("prompt_message_id", 0) or 0):
            await answer(query, "Bu düğme eski bir fotoğrafa ait. Güncel paneli kullan.", show_alert=True)
            return
        if action == "help" and len(parts) == 4:
            await answer(query, f"Fotoğraf çekildiğindeki yaşı 1–120 arasında bir sayı olarak gruba yaz. {int(state['round_seconds'])} saniyen ve tek tahmin hakkın var. Puanlar süre bitince açıklanır.", show_alert=True)
            return
        if action == "status" and len(parts) == 4:
            remaining = max(0, math.ceil(float(state.get("round_deadline") or 0) - time.time()))
            own = state.get("answers", {}).get(str(user.id))
            notice = f"Kalan süre: {remaining} saniye. Bu tur {len(state.get('answers', {}))} tahmin var."
            if own:
                notice += f" Senin tahminin: {int(own['guess'])}."
            await answer(query, notice, show_alert=True)
            return
        if action in {"choose", "band", "bands"}:
            if round_expired(state):
                await answer(query, "Turun süresi doldu. Sonuçlar hazırlanıyor.", show_alert=True)
                return
            if str(user.id) in state.get("answers", {}):
                await answer(query, "Bu turdaki tahminin kaydedildi.", show_alert=True)
                return
            if action in {"choose", "bands"}:
                markup = guess_age_band_keyboard(state, user_id=int(user.id))
                text = f"{str(user.first_name or 'Oyuncu')[:32]}, yaş aralığını seç. İstersen doğrudan sayı da yazabilirsin."
            elif action == "band" and len(parts) in {5, 6}:
                low = int(parts[-1])
                markup = guess_age_number_keyboard(state, user_id=int(user.id), low=low)
                text = f"{str(user.first_name or 'Oyuncu')[:32]}, {low}–{low + 19} aralığından tahminini seç."
            else:
                raise ValueError("invalid_age_action")
            if personal:
                try:
                    await query.edit_message_text(text=text, reply_markup=markup, parse_mode=None)
                except BadRequest as error:
                    if "message is not modified" not in str(error).lower():
                        await answer(query, "Seçim paneli yenilenemedi. Tekrar dene.", show_alert=True)
                        return
                except TelegramError:
                    await answer(query, "Seçim paneli yenilenemedi. Tekrar dene.", show_alert=True)
                    return
                await answer(query)
                return
            previous_id = int(state.get("choice_prompts", {}).get(str(user.id), 0) or 0)
            if previous_id:
                try:
                    await context.bot.edit_message_text(chat_id=int(chat.id), message_id=previous_id,
                        text=text, reply_markup=markup, parse_mode=None)
                    await answer(query)
                    return
                except BadRequest as error:
                    if "message is not modified" in str(error).lower():
                        await answer(query)
                        return
                except TelegramError:
                    pass
            prompt = await query.message.reply_text(text, reply_markup=markup, parse_mode=None)
            try:
                previous = await handler.guess_age_service.set_choice_prompt(int(chat.id), int(user.id), int(prompt.message_id), round_id=state["round_id"], round_no=int(state["round"]))
            except Exception:
                try:
                    await prompt.delete()
                except Exception:
                    pass
                raise
            if previous:
                try:
                    await context.bot.delete_message(chat_id=int(chat.id), message_id=int(previous))
                except Exception:
                    pass
            await answer(query)
            return
        if action == "dismiss" and len(parts) == 5 and parts[4] == str(user.id):
            await query.message.delete()
            await answer(query)
            return
        if action == "stop" and len(parts) == 4:
            if int(state["owner_id"]) != int(user.id) and not await handler._is_group_admin(update, context):
                await answer(query, "Oyunu başlatan kişi veya grup yöneticisi bitirebilir.", show_alert=True)
                return
            await handler.guess_age_service.cancel(int(chat.id), expected_round_id=state["round_id"], expected_round=int(state["round"]))
            await query.edit_message_caption(caption="🎂 <b>Yaş Tahmini</b>\n\nMaç kapatıldı.", parse_mode="HTML", reply_markup=None)
            await answer(query, "Maç kapatıldı.")
            return
        if action != "pick" or len(parts) != 6 or parts[4] != str(user.id):
            await answer(query, "Bu seçim başka bir oyuncuya ait.", show_alert=True)
            return
        # The atomic one-answer-per-player transition owns idempotency. A
        # separate pre-claim would consume a retry before storage succeeds.
        changed, _ = await handler.guess_age_service.submit(chat_id=int(chat.id), user_id=int(user.id),
            display_name=str(user.first_name or "Oyuncu"), guess=parts[5], expected_round_id=parts[1], expected_round=int(parts[2]))
        await answer(query)
        await refresh_age_caption(context.bot, changed)
        try:
            await query.message.delete()
        except Exception:
            pass
    except (ValueError, GuessAgeServiceError) as error:
        code = getattr(error, "code", str(error))
        message = {"guess_age_already_answered": "Bu turdaki tahminin zaten kaydedildi.",
                   "GUESS_AGE_ROUND_EXPIRED": "Turun süresi doldu. Sonuçlar hazırlanıyor.",
                   "GUESS_AGE_ROUND_PREPARING": "Sonraki fotoğraf hazırlanıyor.",
                   "guess_age_full": "Bu maç 100 oyuncuya ulaştı. Sonraki maça katılabilirsin.",
                   "guess_age_out_of_range": "1 ile 120 arasında bir yaş seç."}.get(code, "Tahmin kaydedilemedi. Güncel oyunu /yas ile açabilirsin.")
        await answer(query, message, show_alert=True)
