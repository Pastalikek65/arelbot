from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from src.games.akinator import (
    ANSWER_CHOICES,
    new_akinator_state,
    normalize_akinator_theme,
    sync_akinator_state,
)
from src.services.akinator_service import AkinatorService, AkinatorServiceError
from src.services.akinator_ui import akinator_keyboard, render_akinator_text


class FakeAkinator:
    def __init__(self):
        self.question = "Bu bir karakter mi? <b>"
        self.progression = "12.5"
        self.step = 0
        self.win = False
        self.finished = False
        self.name_proposition = "Arel"
        self.description_proposition = "Dijital bir oyun arkadaşı"
        self.akinator_image_url = None
        self.calls: list[tuple[str, object | None]] = []

    async def start_game(self, **kwargs):
        self.calls.append(("start", kwargs))
        return self

    async def answer(self, choice):
        self.calls.append(("answer", choice))
        self.step += 1
        self.question = "Bir uygulama mı?"
        self.progression = "24.0"
        return self

    async def back(self):
        self.calls.append(("back", None))
        self.step = max(0, self.step - 1)
        self.question = "Bu bir karakter mi?"
        self.progression = "10.0"
        return self

    async def choose(self):
        self.calls.append(("choose", None))
        self.finished = True
        self.win = True
        return self

    async def exclude(self):
        self.calls.append(("exclude", None))
        self.win = False
        self.question = "Başka bir tahmin için devam edelim mi?"
        return self

    async def close(self):
        self.calls.append(("close", None))


def test_akinator_state_and_theme_normalization_are_bounded():
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    assert state["status"] == "pending"
    assert state["theme"] == "c"
    assert normalize_akinator_theme("karakter") == "c"
    assert normalize_akinator_theme("bilinmeyen") == "c"
    assert set(ANSWER_CHOICES) == {"0", "1", "2", "3", "4"}


@pytest.mark.asyncio
async def test_akinator_service_transitions_answer_back_and_proposition():
    fake = FakeAkinator()
    service = AkinatorService(aki_factory=lambda: fake, timeout_seconds=2)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)

    await service.start(state)
    assert state["status"] == "playing"
    assert state["question"] == "Bu bir karakter mi? <b>"

    await service.answer(state, "0")
    assert state["questions"] == 1
    assert state["revision"] == 1
    assert fake.calls[-1] == ("answer", "0")

    await service.answer(state, "b")
    assert state["questions"] == 0
    assert state["revision"] == 2
    assert fake.calls[-1] == ("back", None)

    fake.win = True
    sync_akinator_state(state, fake)
    assert state["status"] == "proposition"
    await service.confirm(state, True)
    assert state["status"] == "finished"
    assert fake.calls[-1] == ("choose", None)


def test_akinator_keyboard_and_renderer_are_turkish_and_round_scoped():
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    state.update({"status": "playing", "question": 'Kim <b>olabilir?</b>', "revision": 4})
    markup = akinator_keyboard(state)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert all(callback.startswith(f"akinator:{state['round_id']}:") for callback in callbacks)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert "Evet" in labels and "Hayır" in labels and "Geri al" in labels
    rendered = render_akinator_text(state)
    assert "Kim &lt;b&gt;olabilir?&lt;/b&gt;" in rendered
    assert "Akinatör" in rendered


@pytest.mark.asyncio
async def test_akinator_rejects_stale_question_without_advancing_client():
    fake = FakeAkinator()
    service = AkinatorService(aki_factory=lambda: fake, timeout_seconds=2)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    await service.start(state)
    await service.answer(state, "0", expected_revision=0)
    with pytest.raises(Exception, match="AKINATOR_STALE_ACTION"):
        await service.answer(state.copy(), "1", expected_revision=0)
    assert [call for call in fake.calls if call[0] == "answer"] == [("answer", "0")]


def test_akinator_service_exposes_lost_network_session():
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    first = AkinatorService(aki_factory=FakeAkinator)
    assert not first.has_session(state)


@pytest.mark.asyncio
async def test_akinator_command_restarts_persisted_round_when_network_session_is_gone():
    from types import SimpleNamespace
    from src.handlers.command_handler import CommandHandler

    old = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    old.update({"status": "playing", "question": "Eski soru?"})

    class Port:
        def __init__(self):
            self.state = old
        async def get_game_state(self, chat_id, game_type):
            return self.state
        async def save_game_state(self, chat_id, game_type, state):
            self.state = state

    class Message:
        message_thread_id = None
        def __init__(self):
            self.replies = []
        async def reply_text(self, text, **kwargs):
            self.replies.append(text)
            async def edit_text(*args, **kwargs):
                return None
            return SimpleNamespace(message_id=12, edit_text=edit_text)

    port = Port()
    service = AkinatorService(aki_factory=FakeAkinator)
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=port, akinator_service=service)
    message = Message()
    update = SimpleNamespace(effective_message=message, message=message,
                             effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=10, first_name="Ada"))
    context = SimpleNamespace(chat_data={}, args=[])
    await handler.handle_akinator_command(update, context)
    assert port.state["round_id"] != old["round_id"]
    assert port.state["status"] == "playing"
    assert service.has_session(port.state)


@pytest.mark.asyncio
async def test_akinator_resume_tracks_the_newest_visible_panel():
    from src.handlers.command_handler import CommandHandler

    class Port:
        async def get_game_state(self, *_args):
            return state
        async def save_game_state(self, _chat_id, _game_type, value):
            assert value is state

    service = AkinatorService(aki_factory=FakeAkinator)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    await service.start(state)
    state["prompt_message_id"] = 12
    message = SimpleNamespace(message_thread_id=None,
                              reply_text=AsyncMock(return_value=SimpleNamespace(message_id=29)))
    update = SimpleNamespace(effective_message=message, message=message,
                             effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=10, first_name="Ada"))
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None,
                             data_port=Port(), akinator_service=service)
    await handler.handle_akinator_command(update, SimpleNamespace(chat_data={}))
    assert state["prompt_message_id"] == 29
    assert state["panel_dirty"] is False


@pytest.mark.asyncio
async def test_akinator_callback_shows_processing_state_while_provider_is_waiting():
    import asyncio
    from types import SimpleNamespace
    from src.handlers.callback_handler import CallbackHandler

    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    state.update({"status": "playing", "question": "İlk soru?", "prompt_message_id": 12})

    class Port:
        async def save_game_state(self, chat_id, game_type, value):
            assert value is state
    class Ledger:
        async def claim(self, *args, **kwargs):
            return True
    class Query:
        message = SimpleNamespace(message_id=12, message_thread_id=None)
        def __init__(self):
            self.edits = []
        async def answer(self, *args, **kwargs):
            return None
        async def edit_message_text(self, text, **kwargs):
            self.edits.append(text)
    class WaitingService:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.lock = asyncio.Lock()
        def presentation_lock(self, chat_id):
            return self.lock
        async def answer(self, value, choice, *, expected_revision):
            self.started.set()
            await self.release.wait()
            value.update(revision=1, question="İkinci soru?")
        async def close(self, value):
            return None

    service = WaitingService()
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=99), None,
                              data_port=Port(), game_action_ledger=Ledger(), akinator_service=service)
    query = Query()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=10), effective_chat=SimpleNamespace(id=-100))
    context = SimpleNamespace(chat_data={"akinator": state})
    callback = f"akinator:{state['round_id']}:0:answer:0"
    pending = asyncio.create_task(handler._handle_akinator_callback(update, query, context, callback))
    await asyncio.wait_for(service.started.wait(), 1)
    assert query.edits and "işleniyor" in query.edits[0]
    service.release.set()
    await asyncio.wait_for(pending, 1)
    assert "İkinci soru?" in query.edits[-1]


@pytest.mark.asyncio
async def test_akinator_concurrent_start_reuses_one_upstream_session():
    import asyncio
    from types import SimpleNamespace
    from src.handlers.command_handler import CommandHandler

    class SlowClient(FakeAkinator):
        def __init__(self, entered, release):
            super().__init__()
            self.entered, self.release = entered, release
        async def start_game(self, **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().start_game(**kwargs)

    class Port:
        def __init__(self):
            self.state = None
        async def get_game_state(self, chat_id, game_type):
            return self.state
        async def save_game_state(self, chat_id, game_type, state):
            self.state = state

    class Message:
        message_thread_id = None
        async def reply_text(self, text, **kwargs):
            async def edit_text(*args, **kwargs):
                return None
            return SimpleNamespace(message_id=12, edit_text=edit_text)

    entered, release = asyncio.Event(), asyncio.Event()
    clients = []
    def factory():
        client = SlowClient(entered, release)
        clients.append(client)
        return client

    port = Port()
    service = AkinatorService(aki_factory=factory)
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=port, akinator_service=service)
    update = SimpleNamespace(effective_message=Message(), message=Message(),
                             effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=10, first_name="Ada"))
    context = SimpleNamespace(chat_data={}, args=[])
    first = asyncio.create_task(handler.handle_akinator_command(update, context))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(handler.handle_akinator_command(update, context))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert len(clients) == 1
    assert port.state is context.chat_data["akinator"]
    assert service.has_session(port.state)


@pytest.mark.asyncio
async def test_akinator_concurrent_answers_leave_new_question_on_panel():
    import asyncio
    from types import SimpleNamespace
    from src.handlers.callback_handler import CallbackHandler

    class SlowClient(FakeAkinator):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()
        async def answer(self, choice):
            self.entered.set()
            await self.release.wait()
            return await super().answer(choice)

    class Port:
        async def save_game_state(self, chat_id, game_type, state):
            return None
    class Ledger:
        async def claim(self, *args, **kwargs):
            return True
    class Query:
        message = SimpleNamespace(message_id=12, message_thread_id=None)
        def __init__(self, edits):
            self.edits = edits
        async def answer(self, *args, **kwargs):
            return None
        async def edit_message_text(self, text, **kwargs):
            self.edits.append(text)

    client = SlowClient()
    service = AkinatorService(aki_factory=lambda: client)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    await service.start(state)
    state["prompt_message_id"] = 12
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=99), None,
                              data_port=Port(), game_action_ledger=Ledger(), akinator_service=service)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=10), effective_chat=SimpleNamespace(id=-100))
    context = SimpleNamespace(chat_data={"akinator": state})
    edits = []
    callback = f"akinator:{state['round_id']}:0:answer:0"
    first = asyncio.create_task(handler._handle_akinator_callback(update, Query(edits), context, callback))
    await asyncio.wait_for(client.entered.wait(), 1)
    second = asyncio.create_task(handler._handle_akinator_callback(update, Query(edits), context, callback))
    await asyncio.sleep(0)
    client.release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert state["revision"] == 1
    assert "Bir uygulama mı?" in edits[-1]
    assert [call for call in client.calls if call[0] == "answer"] == [("answer", "0")]


@pytest.mark.asyncio
async def test_akinator_queue_wait_is_bounded_by_service_timeout():
    service = AkinatorService(max_concurrency=1, timeout_seconds=5)
    service.timeout_seconds = 0.05
    await service._semaphore.acquire()
    try:
        with pytest.raises(AkinatorServiceError, match="AKINATOR_TIMEOUT"):
            await asyncio.wait_for(service._call(asyncio.sleep, 0), timeout=0.3)
    finally:
        service._semaphore.release()


@pytest.mark.asyncio
async def test_confirmed_guess_finishes_even_if_upstream_choice_receipt_times_out():
    class SlowReceipt(FakeAkinator):
        async def choose(self):
            raise TimeoutError("upstream receipt lost")

    client = SlowReceipt()
    service = AkinatorService(aki_factory=lambda: client)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    await service.start(state)
    client.win = True
    sync_akinator_state(state, client)
    await service.confirm(state, True, expected_revision=0)
    assert state["status"] == "finished"
    assert state["proposition"] == "Arel"


@pytest.mark.asyncio
async def test_akinator_completed_guess_is_sent_when_old_panel_cannot_be_edited():
    from src.handlers.callback_handler import CallbackHandler

    class Port:
        def __init__(self):
            self.state = None
        async def get_game_state(self, *_args):
            return self.state
        async def save_game_state(self, _chat_id, _game_type, value):
            self.state = value

    class Ledger:
        async def claim(self, *_args, **_kwargs):
            return True

    client = FakeAkinator()
    service = AkinatorService(aki_factory=lambda: client)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    await service.start(state)
    client.win = True
    sync_akinator_state(state, client)
    state["prompt_message_id"] = 12
    port = Port()
    port.state = state
    query = SimpleNamespace(message=SimpleNamespace(message_id=12, message_thread_id=None),
                            answer=AsyncMock(), edit_message_text=AsyncMock(
                                side_effect=[None, BadRequest("message to edit not found")]))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=13)))
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=99), None,
                              data_port=port, game_action_ledger=Ledger(), akinator_service=service)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=10),
                             effective_chat=SimpleNamespace(id=-100))
    context = SimpleNamespace(chat_data={"akinator": state}, bot=bot)
    callback = f"akinator:{state['round_id']}:0:guess:yes"
    await handler._handle_akinator_callback(update, query, context, callback)
    assert query.answer.await_count >= 1
    assert port.state["status"] == "finished"
    assert bot.send_message.await_count == 1
    assert "Arel" in bot.send_message.await_args.kwargs["text"]
    assert port.state["prompt_message_id"] == 13
    assert port.state["panel_dirty"] is False


@pytest.mark.asyncio
async def test_akinator_runtime_retries_an_undelivered_result():
    from src.handlers.game_runtime import GameRuntime

    class Port:
        def __init__(self, value):
            self.state = value
        async def get_game_state(self, *_args):
            return self.state
        async def save_game_state(self, _chat_id, _game_type, value):
            self.state = value

    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    state.update(status="proposition", proposition="Arel", prompt_message_id=12,
                 panel_dirty=True, delivery_retry_at=0)
    port = Port(state)
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=BadRequest("message to edit not found")),
                          send_message=AsyncMock(return_value=SimpleNamespace(message_id=14)))
    runtime = GameRuntime(port, akinator=AkinatorService(aki_factory=FakeAkinator))
    await runtime._process(bot, {"chat_id": -100, "game_type": "akinator", "state": state})
    assert bot.send_message.await_count == 1
    assert "Arel" in bot.send_message.await_args.kwargs["text"]
    assert port.state["prompt_message_id"] == 14
    assert port.state["panel_dirty"] is False


def test_upstream_terminal_without_a_named_guess_is_not_reported_as_a_win():
    client = FakeAkinator()
    client.finished = True
    client.name_proposition = ""
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    sync_akinator_state(state, client)
    assert state["status"] == "failed"
    assert state["error_code"] == "AKINATOR_NO_GUESS"
    assert "bir tahmin çıkaramadım" in render_akinator_text(state)
    client.name_proposition = "Önce reddedilmiş tahmin"
    sync_akinator_state(state, client)
    assert state["status"] == "failed"
    assert state["proposition"] is None


@pytest.mark.asyncio
async def test_rejected_last_proposal_does_not_look_like_a_successful_guess():
    class LastProposal(FakeAkinator):
        async def exclude(self):
            self.finished = True
            self.win = False

    client = LastProposal()
    service = AkinatorService(aki_factory=lambda: client)
    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    await service.start(state)
    client.win = True
    sync_akinator_state(state, client)
    await service.confirm(state, False)
    assert state["status"] == "failed"
    assert state["proposition"] is None
    assert "bir tahmin çıkaramadım" in render_akinator_text(state)


@pytest.mark.asyncio
async def test_failed_akinator_delivery_remains_pending_for_runtime_retry():
    from src.services.akinator_delivery import publish_akinator_panel

    class Port:
        def __init__(self, value):
            self.state = value
        async def save_game_state(self, _chat_id, _game_type, value):
            self.state = value

    state = new_akinator_state(owner_id=10, owner_name="Ada", chat_id=-100)
    state.update(status="proposition", proposition="Arel", prompt_message_id=12,
                 panel_dirty=True)
    port = Port(state)
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=BadRequest("message to edit not found")),
                          send_message=AsyncMock(side_effect=RuntimeError("transport down")))
    with pytest.raises(RuntimeError, match="transport down"):
        await publish_akinator_panel(bot, port, state)
    assert port.state["panel_dirty"] is True
    assert port.state["delivery_retry_at"] > 0
    assert port.state["delivery_failures"] == 1

