from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.guess_age_service import GuessAgeService
from src.services.uno_service import UnoService


class Repository:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return deepcopy(self.states.get((chat_id, game_type)))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(chat_id, game_type)] = deepcopy(state)

    async def create_game_state_if_absent(self, chat_id, game_type, state):
        if (chat_id, game_type) in self.states:
            return False
        self.states[(chat_id, game_type)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat_id, game_type, state, *, expected_version):
        current = self.states.get((chat_id, game_type))
        if not current or current.get("version") != expected_version:
            return False
        assert state["version"] == expected_version + 1
        self.states[(chat_id, game_type)] = deepcopy(state)
        return True

    async def initialize_game_state_version_if_missing(self, chat_id, game_type):
        current = self.states.get((chat_id, game_type))
        if current is None or "version" in current:
            return False
        current["version"] = 0
        return True

    async def list_due_game_states(self, **kwargs):
        return [{"chat_id": chat, "game_type": game, "state": deepcopy(state)} for (chat, game), state in self.states.items()]


@pytest.mark.asyncio
async def test_periodic_runtime_advances_uno_without_any_player_click():
    from src.handlers.game_runtime import GameRuntime
    repository = Repository()
    uno = UnoService(repository)
    await uno.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await uno.join(chat_id=-100, user_id=2, name="İki")
    await uno.begin(chat_id=-100, user_id=1, action_id="start")
    state = repository.states[(-100, "uno")]
    state.update(turn_expires_at=0, prompt_message_id=10,
                 panel_turn_player_id=state["current_player_id"])
    actor = state["current_player_id"]
    before = len(next(p for p in state["players"] if p["user_id"] == actor)["hand"])
    draw_count = int(state.get("pending_draw", 0)) or 1
    bot = SimpleNamespace(edit_message_text=AsyncMock(return_value=True),
                          send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)),
                          delete_message=AsyncMock())
    runtime = GameRuntime(repository, uno=uno)
    await runtime.tick(SimpleNamespace(bot=bot))
    state = repository.states[(-100, "uno")]
    assert state["current_player_id"] != actor
    assert len(next(p for p in state["players"] if p["user_id"] == actor)["hand"]) == before + draw_count
    assert bot.send_message.await_count == 1 and bot.delete_message.await_count == 1
    assert bot.edit_message_text.await_count == 0 and state["panel_dirty"] is False
    assert state["prompt_message_id"] == 11
    assert state["panel_turn_player_id"] == state["current_player_id"]


@pytest.mark.asyncio
async def test_age_round_resumes_after_restart_and_retries_failed_photo(tmp_path):
    from src.handlers.game_runtime import GameRuntime
    repository = Repository()
    age = GuessAgeService(repository, asset_dir=tmp_path)
    for asset in age.assets:
        (tmp_path / asset.filename).write_bytes(b"portrait")
    await age.start(chat_id=-100, owner_id=1, owner_name="Bir")
    state = repository.states[(-100, "guess_age")]
    state.update(round_started_at="2000-01-01T00:00:00+00:00", round_deadline=0, prompt_message_id=10,
                 initial_delivery_pending=False)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)),
                          send_photo=AsyncMock(side_effect=[RuntimeError("temporary"), SimpleNamespace(message_id=12)]),
                          edit_message_reply_markup=AsyncMock())
    runtime = GameRuntime(repository, guess_age=age)
    await runtime.tick(SimpleNamespace(bot=bot))
    pending = repository.states[(-100, "guess_age")]
    assert pending["round"] == 2 and pending["delivery_pending"]
    assert bot.send_photo.await_count == 0  # Result reading time precedes the next photo.
    assert pending["result_visible_until"] > 0
    repository.states[(-100, "guess_age")]["result_visible_until"] = 0
    repository.states[(-100, "guess_age")]["delivery_retry_at"] = 0
    # A new runtime object represents a process restart; no in-memory timer exists.
    runtime = GameRuntime(repository, guess_age=GuessAgeService(repository, asset_dir=tmp_path))
    await runtime.tick(SimpleNamespace(bot=bot))
    assert repository.states[(-100, "guess_age")]["delivery_pending"]  # first photo attempt failed
    repository.states[(-100, "guess_age")]["delivery_retry_at"] = 0
    await runtime.tick(SimpleNamespace(bot=bot))
    state = repository.states[(-100, "guess_age")]
    assert state["round"] == 2 and not state["delivery_pending"]
    assert state["prompt_message_id"] == 12 and state["round_deadline"] > 0
    assert bot.send_message.await_count == 1  # Already delivered result is not repeated.


@pytest.mark.asyncio
async def test_late_old_panel_delivery_schedules_a_repair_of_the_current_panel():
    from src.handlers.game_runtime import GameRuntime
    repository = Repository()
    uno = UnoService(repository)
    old = await uno.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await uno.set_prompt_message(chat_id=-100, message_id=10)
    old["prompt_message_id"] = 10
    async def late_edit(**kwargs):
        await uno.join(chat_id=-100, user_id=2, name="İki")
        current = await uno.current(-100)
        await uno.mark_panel_delivered(-100, current["revision"])
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=late_edit))
    await GameRuntime(repository, uno=uno).publish_uno(bot, -100, old)
    assert repository.states[(-100, "uno")]["panel_dirty"] is True


@pytest.mark.asyncio
async def test_age_command_opens_and_resumes_photo_after_restart_without_resetting_timer(tmp_path):
    from src.handlers.command_handler import CommandHandler
    repository = Repository()
    age = GuessAgeService(repository, asset_dir=tmp_path)
    for asset in age.assets:
        (tmp_path / asset.filename).write_bytes(b"portrait")
    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=[SimpleNamespace(message_id=31), SimpleNamespace(message_id=32)]))
    message = SimpleNamespace(message_thread_id=42, is_topic_message=True, message_id=30, reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"), effective_message=message, message=message)
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=repository, guess_age_service=age)
    await handler.handle_guess_age_command(update, SimpleNamespace(bot=bot))
    first = deepcopy(repository.states[(-100, "guess_age")])
    restarted = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=repository,
                               guess_age_service=GuessAgeService(repository, asset_dir=tmp_path))
    await restarted.handle_guess_age_command(update, SimpleNamespace(bot=bot))
    current = repository.states[(-100, "guess_age")]
    assert current["round_id"] == first["round_id"] and current["prompt_message_id"] == 31
    assert current["round_deadline"] == first["round_deadline"]
    assert bot.send_photo.await_count == 1 and message.reply_text.called
    assert "süreyi yenilemez" in message.reply_text.await_args.args[0]
    message.message_thread_id = 99
    await restarted.handle_guess_age_command(update, SimpleNamespace(bot=bot))
    assert bot.send_photo.await_count == 1 and message.reply_text.called
