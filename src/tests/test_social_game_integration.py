"""Integration boundaries for persistent secret-role Telegram games."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


def test_spy_lobby_shows_private_readiness_without_private_receipt_metadata():
    from src.games import spy
    from src.services.spy_ui import render_spy
    current = spy.new_lobby(chat_id=-100,thread_id=None,owner_id=7,owner_name="Ada",round_id="r",now=1)
    current["dm_ready"] = [7]
    current["players"][0]["dm_message_id"] = 999
    view = spy.public_state(current)
    assert view["players"][0]["ready"]
    assert "dm_message_id" not in view["players"][0]
    assert "Özel sohbeti hazır: 1/1" in render_spy(current)


@pytest.mark.parametrize("game,key", [("spy", "casus_t42"), ("vampire", "vampir_koylu")])
@pytest.mark.asyncio
async def test_restart_runtime_dispatches_secret_game_to_its_durable_adapter(game, key):
    from src.handlers.game_runtime import GameRuntime
    adapter = SimpleNamespace(process=AsyncMock())
    runtime = GameRuntime(SimpleNamespace())
    setattr(runtime, game + "_handler", adapter)
    row = {"chat_id": -100, "game_type": key, "state": {"thread_id": 42}}
    await runtime._process(SimpleNamespace(), row)
    adapter.process.assert_awaited_once()
    assert adapter.process.await_args.args[1] is row


def test_role_games_are_reachable_from_the_group_game_category():
    from src.services.menu_service import MenuService
    text, keyboard = MenuService().games_menu(category="social")
    actions = {button.callback_data for row in keyboard.inline_keyboard for button in row}
    assert {"game_start_tabu", "game_start_casus", "game_start_vampire"} <= actions
    assert "7" in text and "4" in text


@pytest.mark.parametrize("prefix,game", [("sp:", "spy"), ("vp:", "vampire")])
@pytest.mark.asyncio
async def test_private_role_callback_reaches_game_without_generic_ack(prefix, game):
    from src.handlers.callback_handler import CallbackHandler
    handler = CallbackHandler.__new__(CallbackHandler)
    adapter = SimpleNamespace(callback=AsyncMock())
    setattr(handler, game + "_handler", adapter)
    query = SimpleNamespace(data=prefix + "bound-action", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=7))
    await handler.handle_callback_query(update, SimpleNamespace())
    adapter.callback.assert_awaited_once()
    query.answer.assert_not_awaited()


@pytest.mark.parametrize("prefix,game", [("sp_", "spy"), ("vp_", "vampire")])
@pytest.mark.asyncio
async def test_private_invitation_routes_before_general_deep_link_handling(prefix, game):
    from src.orchestrator import Orchestrator
    handler = Orchestrator.__new__(Orchestrator)
    adapter = SimpleNamespace(deep_link=AsyncMock(return_value=True))
    setattr(handler, game + "_handler", adapter)
    assert await handler._handle_deep_link(SimpleNamespace(), SimpleNamespace(), prefix + "bound-token")
    adapter.deep_link.assert_awaited_once()


@pytest.mark.parametrize("method", ["handle_caption", "handle_animation"])
@pytest.mark.asyncio
async def test_tabu_caption_is_checked_before_any_media_or_ai_work(method):
    from src.orchestrator import Orchestrator
    handler = Orchestrator.__new__(Orchestrator)
    handler.media_handler = SimpleNamespace(moderate_update=AsyncMock(return_value=False),handle_photo=AsyncMock())
    handler.tabu_handler = SimpleNamespace(on_text=AsyncMock(return_value=True))
    handler._queue_media_download = AsyncMock()
    incoming = SimpleNamespace(caption="yasak https://youtube.com/watch?v=example", animation=object(),photo=[object()])
    update = SimpleNamespace(effective_message=incoming, effective_chat=SimpleNamespace(type="supergroup"))
    await getattr(handler, method)(update, SimpleNamespace())
    handler.tabu_handler.on_text.assert_awaited_once()
    handler._queue_media_download.assert_not_awaited()
    handler.media_handler.handle_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_caption_edit_reaches_the_tabu_narrator_check():
    from src.handlers.message_handler import MessageHandler
    handler = MessageHandler.__new__(MessageHandler)
    handler.tabu_handler = SimpleNamespace(on_text=AsyncMock(return_value=True))
    message = SimpleNamespace(text=None, caption="yasak ifade")
    update = SimpleNamespace(edited_message=message)
    await handler.handle_message(update, SimpleNamespace())
    handler.tabu_handler.on_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_role_text_is_never_sent_to_ai_history():
    from src.handlers.message_handler import MessageHandler
    handler = MessageHandler.__new__(MessageHandler)
    handler.moderation = None
    handler.data_port = SimpleNamespace(get_active_secret_game_for_user=AsyncMock(return_value={"game_type": "casus"}))
    handler.chat_engine = SimpleNamespace(record_message=AsyncMock())
    incoming = SimpleNamespace(text="Benim gizli rolüm ve mekânım", reply_text=AsyncMock())
    update = SimpleNamespace(message=incoming, effective_message=incoming,
        effective_chat=SimpleNamespace(id=7, type="private"), effective_user=SimpleNamespace(id=7))
    await handler.handle_message(update, SimpleNamespace())
    handler.chat_engine.record_message.assert_not_awaited()
    incoming.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_admin_stop_command_keeps_other_players_legacy_game(monkeypatch):
    from src.handlers.command_handler import CommandHandler
    monkeypatch.setattr("src.handlers.command_handler.is_admin", AsyncMock(return_value=False))
    port = SimpleNamespace(get_game_state=AsyncMock(return_value=None),delete_game_state=AsyncMock())
    handler = CommandHandler(SimpleNamespace(),SimpleNamespace(owner_id=999),None,None,data_port=port)
    incoming = SimpleNamespace(message_id=9,reply_text=AsyncMock())
    update = SimpleNamespace(message=incoming,effective_message=incoming,effective_user=SimpleNamespace(id=7),effective_chat=SimpleNamespace(id=-100,type="supergroup"))
    game={"owner_id":8,"status":"playing"}
    context=SimpleNamespace(chat_data={"hangman":game},job_queue=SimpleNamespace(get_jobs_by_name=lambda name:[]))
    await handler.handle_oyunbitir_command(update,context)
    assert context.chat_data.get("hangman") is game
    port.delete_game_state.assert_not_awaited()


@pytest.mark.parametrize("kind",["spy","vampire","tabu"])
@pytest.mark.asyncio
async def test_admin_menu_stop_cancels_secret_games_through_domain(kind,monkeypatch):
    from src.handlers.callback_handler import CallbackHandler
    monkeypatch.setattr("src.handlers.callback_handler.is_admin",AsyncMock(return_value=True))
    port=SimpleNamespace(get_game_state=AsyncMock(return_value=None),delete_game_state=AsyncMock())
    handler=CallbackHandler(SimpleNamespace(),SimpleNamespace(owner_id=999),None,data_port=port)
    service=SimpleNamespace(current=AsyncMock(return_value={"phase":"lobby","round_id":"bound","card_id":None}),act=AsyncMock())
    adapter=SimpleNamespace(service=service,_flush=AsyncMock())
    setattr(handler,kind+"_handler",adapter)
    incoming=SimpleNamespace(message_id=9)
    query=SimpleNamespace(id="stop",message=incoming,answer=AsyncMock())
    update=SimpleNamespace(effective_message=incoming,effective_user=SimpleNamespace(id=7),effective_chat=SimpleNamespace(id=-100,type="supergroup"))
    await handler._handle_game_stop(update,query,SimpleNamespace(chat_data={}))
    service.act.assert_awaited_once()
    assert service.act.await_args.kwargs["action"]=="stop"
    assert service.act.await_args.kwargs["round_id"]=="bound"
    adapter._flush.assert_awaited_once()
