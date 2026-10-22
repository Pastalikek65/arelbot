from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.guess_age_ui import guess_age_keyboard, render_guess_age_text


def state():
    return {
        "round_id": "a" * 20, "round": 1, "rounds": 5, "status": "playing", "chat_id": -100,
        "thread_id": None, "prompt_message_id": 100,
        "round_started_at": "2026-10-21T10:00:00.900000+00:00",
        "round_deadline": 1792576830, "round_seconds": 30,
        "asset_credit": "Photographer · CC BY-SA 4.0",
        "asset_source_url": "https://commons.wikimedia.org/wiki/File:Secret_Person.jpg",
        "players": {}, "answers": {},
    }


def message(**kwargs):
    return SimpleNamespace(
        **dict({"message_id": 101, "is_topic_message": False, "message_thread_id": 99,
                "date": datetime(2026, 10, 21, 10, 0, 1, tzinfo=timezone.utc),
                "reply_to_message": None}, **kwargs)
    )


def test_active_caption_promotes_plain_input_and_does_not_reveal_identity_link():
    text = render_guess_age_text(state())
    assert "sayı" in text and "30" in text
    assert "Secret_Person" not in text
    assert "Photographer" in text and "CC BY-SA 4.0" in text
    callbacks = [b.callback_data for row in guess_age_keyboard(state()).inline_keyboard for b in row]
    assert len(callbacks) <= 5
    assert not any(":band:" in callback for callback in callbacks)


def test_group_age_surfaces_have_no_links_and_private_directory_keeps_attribution():
    from src.games.guess_age import AgeAsset
    from src.services.guess_age_ui import render_guess_age_round_result, render_guess_age_photo_answer, render_age_sources
    current = state()
    current["asset_license_url"] = "https://creativecommons.org/licenses/by-sa/4.0/"
    current["history"] = [dict(current, person_name="Ada", target_age=35, photo_date="2020-01-01", ranked=[])]
    for text in (render_guess_age_text(current), render_guess_age_round_result(current, []), render_guess_age_photo_answer(current)):
        assert "https://" not in text and "href=" not in text
    current["asset_credit"] = "Ada http://www.example.org/photo · CC BY 4.0"
    current["history"][-1]["asset_credit"] = current["asset_credit"]
    for text in (render_guess_age_text(current), render_guess_age_photo_answer(current)):
        assert "example.org" not in text and "Ada" in text
    asset = AgeAsset("x", "x.jpg", 35, "Ada", "2020-01-01", current["asset_source_url"], current["asset_credit"],
                     license_url=current["asset_license_url"])
    text, keyboard = render_age_sources([asset] * 4)
    assert asset.source_url in text and asset.license_url in text and asset.credit in text
    assert "Sayfa 1/2" in text and keyboard.inline_keyboard[0][0].callback_data == "guessage:sources:1"
    text, keyboard = render_age_sources([asset] * 4, 999)
    assert "Sayfa 2/2" in text and keyboard.inline_keyboard[0][0].text == "Önceki"


@pytest.mark.asyncio
async def test_source_pagination_never_posts_links_in_a_group():
    from unittest.mock import AsyncMock
    from src.handlers.age_surface import handle_age_callback
    handler = SimpleNamespace(_safe_query_answer=AsyncMock())
    query = SimpleNamespace(edit_message_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="supergroup"))
    await handle_age_callback(handler, update, query, SimpleNamespace(), "guessage:sources:0")
    query.edit_message_text.assert_not_awaited()
    assert handler._safe_query_answer.await_args.kwargs["show_alert"]


@pytest.mark.asyncio
async def test_private_source_directory_and_pagination_disable_previews():
    from unittest.mock import AsyncMock
    from src.handlers.age_surface import handle_age_callback
    from src.handlers.command_handler import CommandHandler
    service = SimpleNamespace(assets=[])
    incoming = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=incoming, effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private"))
    handler = SimpleNamespace(guess_age_service=service, _safe_query_answer=AsyncMock())
    await CommandHandler.handle_guess_age_command(handler, update, SimpleNamespace(args=["kaynaklar"]))
    assert incoming.reply_text.await_args.kwargs["disable_web_page_preview"]
    query = SimpleNamespace(edit_message_text=AsyncMock())
    await handle_age_callback(handler, update, query, SimpleNamespace(), "guessage:sources:0")
    assert query.edit_message_text.await_args.kwargs["disable_web_page_preview"]


def test_message_binding_uses_topic_prompt_and_server_round_floor():
    from src.handlers.age_input import is_current_age_message
    current = state()
    assert is_current_age_message(current, message())
    assert not is_current_age_message(current, message(message_id=99))
    assert not is_current_age_message(current, message(is_topic_message=True, message_thread_id=42))
    assert not is_current_age_message(current, message(reply_to_message=SimpleNamespace(message_id=50)))
    assert not is_current_age_message(current, message(date=datetime(2026, 10, 21, 9, 59, 59, tzinfo=timezone.utc)))
    assert is_current_age_message(current, message(date=datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone.utc)))
    assert not is_current_age_message(dict(current, delivery_pending=True), message())
    assert not is_current_age_message(dict(current, initial_delivery_pending=True), message())


def test_topic_input_cannot_cross_into_another_forum_topic():
    from src.handlers.age_input import is_current_age_message
    current = dict(state(), thread_id=42)
    assert is_current_age_message(current, message(is_topic_message=True, message_thread_id=42))
    assert not is_current_age_message(current, message(is_topic_message=True, message_thread_id=43))


@pytest.mark.asyncio
async def test_plain_age_submission_never_publishes_score_and_binds_round():
    import asyncio
    from unittest.mock import AsyncMock
    from src.handlers.message_handler import MessageHandler
    changed = dict(state(), answers={"7": {"guess": 35, "score": 85}}, players={"7": {"score": 85}})
    service = SimpleNamespace(current=AsyncMock(return_value=state()),
                              submit=AsyncMock(return_value=(changed, SimpleNamespace(score=85))),
                              presentation_lock=lambda chat: asyncio.Lock())
    handler = MessageHandler.__new__(MessageHandler)
    handler.guess_age_service = service
    incoming = message(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=incoming, message=incoming,
                             effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=7, first_name="Ada"))
    bot = SimpleNamespace(edit_message_caption=AsyncMock())
    assert await handler._check_age_input(update, "35", SimpleNamespace(bot=bot))
    incoming.reply_text.assert_not_awaited()
    assert service.submit.await_args.kwargs["expected_round_id"] == "a" * 20
    assert service.submit.await_args.kwargs["expected_round"] == 1
    caption = bot.edit_message_caption.await_args.kwargs["caption"]
    assert "Bu tur 1 tahmin" in caption and "Secret_Person" not in caption


def callback_fixture(action, *, personal=False):
    import asyncio
    from unittest.mock import AsyncMock
    from src.handlers.callback_handler import CallbackHandler
    handler = CallbackHandler.__new__(CallbackHandler)
    current = state()
    current.update(round_started_at=datetime.now(timezone.utc).isoformat(), choice_prompts={"7": 200})
    service = SimpleNamespace(current=AsyncMock(return_value=current),
                              submit=AsyncMock(), set_choice_prompt=AsyncMock(return_value=None),
                              presentation_lock=lambda chat: asyncio.Lock())
    handler.guess_age_service = service
    handler._safe_query_answer = AsyncMock()
    query = SimpleNamespace(data=f"guessage:{current['round_id']}:1:{action}",
                            message=message(message_id=200 if personal else 100),
                            answer=AsyncMock(), delete=AsyncMock())
    query.message.delete = AsyncMock()
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=7, first_name="Ada"))
    return handler, update, service


@pytest.mark.asyncio
async def test_age_callback_owns_its_answer_before_the_generic_empty_ack():
    handler, update, service = callback_fixture("help")
    await handler.handle_callback_query(update, SimpleNamespace())
    update.callback_query.answer.assert_not_awaited()
    assert handler._safe_query_answer.await_count == 1
    assert handler._safe_query_answer.await_args.kwargs["show_alert"]
    assert "tek tahmin" in handler._safe_query_answer.await_args.args[1]


@pytest.mark.asyncio
async def test_storage_failure_does_not_spend_a_callback_retry_before_submit():
    from src.handlers.age_surface import handle_age_callback
    from src.services.guess_age_service import GuessAgeServiceError
    handler, update, service = callback_fixture("pick:7:35", personal=True)
    service.submit.side_effect = [GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE"), (state(), object())]
    from unittest.mock import AsyncMock
    context = SimpleNamespace(bot=SimpleNamespace(edit_message_caption=AsyncMock()))
    await handle_age_callback(handler, update, update.callback_query, context, update.callback_query.data)
    await handle_age_callback(handler, update, update.callback_query, context, update.callback_query.data)
    assert service.submit.await_count == 2
    update.callback_query.message.delete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pick", "dismiss", "band", "unknown", "stop:7"])
async def test_malformed_age_actions_are_rejected_before_reading_game_state(action):
    from src.handlers.age_surface import handle_age_callback
    handler, update, service = callback_fixture(action)
    await handle_age_callback(handler, update, update.callback_query, SimpleNamespace(), update.callback_query.data)
    service.current.assert_not_awaited()
    assert handler._safe_query_answer.await_args.kwargs["show_alert"]


@pytest.mark.asyncio
async def test_personal_picker_is_bound_to_actor_and_latest_choice_message():
    from src.handlers.age_surface import handle_age_callback
    handler, update, service = callback_fixture("pick:8:35", personal=True)
    await handle_age_callback(handler, update, update.callback_query, SimpleNamespace(), update.callback_query.data)
    update.callback_query.data = update.callback_query.data.replace("pick:8", "pick:7")
    update.callback_query.message.message_id = 199
    await handle_age_callback(handler, update, update.callback_query, SimpleNamespace(), update.callback_query.data)
    service.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_age_picker_edits_one_personal_panel_instead_of_posting_each_step():
    import time
    from unittest.mock import AsyncMock
    from src.handlers.age_surface import handle_age_callback

    handler, update, service = callback_fixture("band:7:21", personal=True)
    current = service.current.return_value
    current["round_deadline"] = time.time() + 30
    query = update.callback_query
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    context = SimpleNamespace(bot=SimpleNamespace(edit_message_text=AsyncMock()))

    await handle_age_callback(handler, update, query, context, query.data)
    query.edit_message_text.assert_awaited_once()
    query.message.reply_text.assert_not_awaited()
    service.set_choice_prompt.assert_not_awaited()
    number_buttons = [button for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
                      for button in row]
    assert any(button.text == "21" for button in number_buttons)
    assert any(button.text == "◀️ Aralıklar" for button in number_buttons)

    query.data = f"guessage:{current['round_id']}:1:bands:7"
    await handle_age_callback(handler, update, query, context, query.data)
    assert query.edit_message_text.await_count == 2
    query.message.reply_text.assert_not_awaited()
    band_buttons = [button for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
                    for button in row]
    assert any(button.text == "21–40" for button in band_buttons)


@pytest.mark.asyncio
async def test_reopening_age_picker_reuses_existing_personal_message():
    import time
    from unittest.mock import AsyncMock
    from src.handlers.age_surface import handle_age_callback

    handler, update, service = callback_fixture("choose")
    service.current.return_value["round_deadline"] = time.time() + 30
    update.callback_query.message.reply_text = AsyncMock()
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    await handle_age_callback(handler, update, update.callback_query,
                              SimpleNamespace(bot=bot), update.callback_query.data)
    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 200
    update.callback_query.message.reply_text.assert_not_awaited()
    service.set_choice_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_public_band_button_keeps_the_photo_panel_intact():
    import time
    from unittest.mock import AsyncMock
    from src.handlers.age_surface import handle_age_callback

    handler, update, service = callback_fixture("band:21")
    service.current.return_value["round_deadline"] = time.time() + 30
    query = update.callback_query
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    bot = SimpleNamespace(edit_message_text=AsyncMock())

    await handle_age_callback(handler, update, query, SimpleNamespace(bot=bot), query.data)
    query.edit_message_text.assert_not_awaited()
    query.message.reply_text.assert_not_awaited()
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 200
    assert "21–40" in bot.edit_message_text.await_args.kwargs["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [7, 8])
async def test_global_stop_preserves_age_rewards_and_checks_the_owner(monkeypatch, owner):
    from unittest.mock import AsyncMock
    from src.handlers.command_handler import CommandHandler
    monkeypatch.setattr("src.handlers.command_handler.is_admin", AsyncMock(return_value=False))
    port = SimpleNamespace(get_game_state=AsyncMock(return_value=None), delete_game_state=AsyncMock())
    age = SimpleNamespace(current=AsyncMock(return_value=dict(state(), owner_id=owner, pending_scores=[{"score": 80}])),
                          cancel=AsyncMock(return_value=True))
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, None,
                             data_port=port, guess_age_service=age)
    incoming = message(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=incoming, message=incoming,
                             effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=-100, type="supergroup"))
    context = SimpleNamespace(chat_data={}, job_queue=SimpleNamespace(get_jobs_by_name=lambda name: []))
    await handler.handle_oyunbitir_command(update, context)
    assert not any(call.args[1] == "guess_age" for call in port.delete_game_state.await_args_list)
    assert age.cancel.await_count == (1 if owner == 7 else 0)
