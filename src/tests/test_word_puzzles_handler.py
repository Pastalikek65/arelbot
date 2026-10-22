"""Telegram surface and canonical message binding for word puzzles."""
import time
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telegram.error import TelegramError

from src.handlers.word_puzzles_handler import WordPuzzlesHandler
from src.services.word_puzzles_service import WordPuzzlesService
from src.services.word_puzzles_ui import callback_data, keyboard, render_game


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        return deepcopy(self.rows.get((chat, key)))

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        previous = self.rows.get((chat, key))
        if not previous or previous["version"] != expected_version:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True


class Bot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.next_id = 100

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)


class Message:
    def __init__(self, message_id=1, chat_id=-100, thread_id=7, reply_to=None):
        self.message_id, self.chat_id = message_id, chat_id
        self.message_thread_id = thread_id
        self.is_topic_message = thread_id is not None
        self.reply_to_message = reply_to
        self.date = datetime.fromtimestamp(time.time() + 1, UTC)
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=200)


class Query:
    def __init__(self, data, user_id, message):
        self.data, self.message = data, message
        self.id = f"query:{user_id}:{data}"
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def update(user_id, message, query=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=message.chat_id, type="supergroup"),
                           effective_user=SimpleNamespace(id=user_id, first_name=f"U{user_id}"),
                           effective_message=message, callback_query=query, edited_message=None)


@pytest.mark.asyncio
async def test_group_panel_hides_answer_until_reveal_and_text_requires_member():
    svc, bot = WordPuzzlesService(Repository()), Bot()
    handler = WordPuzzlesHandler(svc)
    await svc.open(game_key="anagram", chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    state = await svc.current(game_key="anagram", chat_id=-100, thread_id=7)
    await svc.act(game_key="anagram", chat_id=-100, thread_id=7, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  word_order=list(range(5)))
    state = await svc.current(game_key="anagram", chat_id=-100, thread_id=7)
    assert state["target"] not in render_game(state)
    await handler.publish(bot, game_key="anagram", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="anagram", chat_id=-100, thread_id=7)
    panel = Message(message_id=state["panel_message_id"])
    wrong = Message(message_id=501, reply_to=Message(message_id=999))
    ctx = SimpleNamespace(bot=bot)
    assert not await handler.on_text(update(1, wrong), ctx, state["target"])
    outsider = Message(message_id=502, reply_to=panel)
    assert not await handler.on_text(update(2, outsider), ctx, state["target"])
    message = Message(message_id=503, reply_to=panel)
    assert await handler.on_text(update(1, message), ctx, state["target"])
    state = await svc.current(game_key="anagram", chat_id=-100, thread_id=7)
    assert state["phase"] == "reveal"
    assert state["target"] in render_game(state)


@pytest.mark.asyncio
async def test_member_invalid_wordle_guess_is_consumed_before_ai_route():
    svc, bot = WordPuzzlesService(Repository()), Bot()
    handler = WordPuzzlesHandler(svc)
    await svc.open(game_key="wordle_tr", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    state = await svc.current(game_key="wordle_tr", chat_id=-100, thread_id=7)
    await svc.act(game_key="wordle_tr", chat_id=-100, thread_id=7,
                  user_id=1, action="start", action_id="start",
                  round_id=state["round_id"], target="kalem")
    await handler.publish(bot, game_key="wordle_tr", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="wordle_tr", chat_id=-100, thread_id=7)
    panel = Message(message_id=state["panel_message_id"])
    assert await handler.on_text(update(1, Message(message_id=501, reply_to=panel)),
                                 SimpleNamespace(bot=bot), "zzzzz")
    state = await svc.current(game_key="wordle_tr", chat_id=-100, thread_id=7)
    assert state["phase"] == "playing" and state["attempts"] == []


@pytest.mark.asyncio
async def test_callback_rejects_other_topic_and_old_panel():
    svc, bot = WordPuzzlesService(Repository()), Bot()
    handler = WordPuzzlesHandler(svc)
    await svc.open(game_key="karsi_kelime", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    await handler.publish(bot, game_key="karsi_kelime", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="karsi_kelime", chat_id=-100, thread_id=7)
    wrong = Message(message_id=state["panel_message_id"], thread_id=8)
    query = Query(callback_data(state, "join"), 2, wrong)
    assert await handler.callback(update(2, wrong, query), SimpleNamespace(bot=bot))
    assert query.answers[-1][1].get("show_alert")
    old = Message(message_id=state["panel_message_id"] - 1, thread_id=7)
    q2 = Query(callback_data(state, "join"), 2, old)
    assert await handler.callback(update(2, old, q2), SimpleNamespace(bot=bot))
    assert q2.answers[-1][1].get("show_alert")


def test_terminal_panel_has_replay_and_games_menu_actions():
    from src.games import word_puzzles as games
    state = games.new_lobby(game_key="wordle_tr", chat_id=-100, thread_id=None,
                            owner_id=1, owner_name="Bir")
    state["phase"] = "finished"
    buttons = keyboard(state).inline_keyboard[0]
    assert {button.callback_data for button in buttons} == {
        "game_start_wordle_tr", "menu_games", "gh:wordle_tr"}


def test_thirty_html_heavy_names_fit_telegram_panel():
    from src.games import word_puzzles as games
    state = games.new_lobby(game_key="karsi_kelime", chat_id=-100, thread_id=None,
                            owner_id=1, owner_name="<&>" * 20)
    for user_id in range(2, 31):
        state = games.join(state, user_id=user_id, user_name="<&>" * 20)
    text = render_game(state)
    assert len(text) <= 4096
    assert "<b><&>" not in text


@pytest.mark.asyncio
async def test_cancel_for_cancels_every_owned_active_game_in_scope():
    svc, bot = WordPuzzlesService(Repository()), Bot()
    handler = WordPuzzlesHandler(svc)
    for game_key in ("anagram", "karsi_kelime"):
        await svc.open(game_key=game_key, chat_id=-100, thread_id=7,
                       user_id=1, user_name="Bir")
    assert await handler.cancel_for(update(1, Message(message_id=300)),
                                    SimpleNamespace(bot=bot))
    for game_key in ("anagram", "karsi_kelime"):
        state = await svc.current(game_key=game_key, chat_id=-100, thread_id=7)
        assert state["phase"] == "cancelled"


@pytest.mark.asyncio
async def test_failed_panel_delivery_keeps_lease_and_retries_without_timer():
    class FailedBot(Bot):
        async def send_message(self, **kwargs):
            raise TelegramError("transient")

    svc = WordPuzzlesService(Repository())
    handler = WordPuzzlesHandler(svc)
    await svc.open(game_key="wordle_tr", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    state = await svc.current(game_key="wordle_tr", chat_id=-100, thread_id=7)
    await svc.act(game_key="wordle_tr", chat_id=-100, thread_id=7,
                  user_id=1, action="start", action_id="start",
                  round_id=state["round_id"], target="kalem")
    with pytest.raises(TelegramError):
        await handler.publish(FailedBot(), game_key="wordle_tr", chat_id=-100,
                              thread_id=7)
    state = await svc.current(game_key="wordle_tr", chat_id=-100, thread_id=7)
    assert state["panel_dirty"] and state["deadline"] is None
    assert await handler.publish(Bot(), game_key="wordle_tr", chat_id=-100,
                                 thread_id=7, force=True) is False
    assert await svc.claim_panel(game_key="wordle_tr", chat_id=-100,
                                 thread_id=7, now=time.time() + 31)


@pytest.mark.asyncio
async def test_force_refresh_edits_clean_canonical_panel_via_lease():
    svc, bot = WordPuzzlesService(Repository()), Bot()
    handler = WordPuzzlesHandler(svc)
    await svc.open(game_key="anagram", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    assert await handler.publish(bot, game_key="anagram", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="anagram", chat_id=-100, thread_id=7)
    assert not state["panel_dirty"]
    assert await handler.publish(bot, game_key="anagram", chat_id=-100,
                                 thread_id=7, force=True)
    current = await svc.current(game_key="anagram", chat_id=-100, thread_id=7)
    assert current["panel_message_id"] == state["panel_message_id"]
    assert len(bot.sent) == 1 and len(bot.edited) == 1
