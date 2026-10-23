"""The deployed default uses the source engine through signed inline transport."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.uno_service import UnoService
from src.services.uno_inline_service import UnoInlineService
from src.services.uno_ui import uno_keyboard
from src.games import uno_upstream


class Database:
    def __init__(self):
        self.state = None

    async def get_game_state(self, *args):
        return json.loads(json.dumps(self.state))

    async def save_game_state(self, chat, game, state):
        self.state = json.loads(json.dumps(state))


@pytest.mark.asyncio
async def test_new_match_inline_choice_uses_source_engine_and_old_match_remains_legacy():
    database = Database()
    service = UnoService(database, inline_secret=b"x" * 32)
    service.bot_username = "ArelChat_Bot"
    await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-100, user_id=2, name="İki")
    state = await service.begin(chat_id=-100, user_id=1, action_id="begin")
    assert state["engine"] == "maildian" and uno_upstream.validate_state(state)
    buttons = [b for row in uno_keyboard(state).inline_keyboard for b in row]
    assert any(b.switch_inline_query_current_chat == state["inline_query"] for b in buttons)
    assert not any(":playpos:" in (b.callback_data or "") for b in buttons)
    actor = state["current_player_id"]
    controller = UnoInlineService(service, secret=b"x" * 32, file_ids={})
    assert controller.create_query(state) == state["inline_query"]
    result_id = controller._encode_choice(state, actor, "draw", None)
    bot = SimpleNamespace(edit_message_text=AsyncMock(),
                          send_message=AsyncMock(return_value=SimpleNamespace(message_id=8)),
                          delete_message=AsyncMock())
    await service.set_prompt_message(chat_id=-100, message_id=7)
    choice = SimpleNamespace(result_id=result_id, from_user=SimpleNamespace(id=actor), query=state["inline_query"])
    await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice), SimpleNamespace(bot=bot))
    changed = await service.current(-100)
    assert changed["revision"] == state["revision"] + 1
    assert uno_upstream.validate_state(changed)
    await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice), SimpleNamespace(bot=bot))
    assert (await service.current(-100))["revision"] == changed["revision"]
    if changed["status"] == "playing" and changed["current_player_id"] != actor:
        assert bot.send_message.await_count == 1
        assert bot.edit_message_text.await_count == 0
        assert bot.delete_message.await_count == 1
        assert changed["prompt_message_id"] == 8
    else:
        assert bot.edit_message_text.await_count == 1
        assert bot.send_message.await_count == 0

    legacy_db = Database()
    legacy = UnoService(legacy_db, ruleset="legacy")
    await legacy.start(chat_id=-100, owner_id=1, owner_name="Bir")
    assert "engine" not in legacy_db.state
    assert (await UnoService(legacy_db).current(-100))["status"] == "lobby"
