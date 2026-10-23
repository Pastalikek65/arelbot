"""A group drawing match keeps the word private until each sketch is scored."""

import pytest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone

from src.games import social_arcade as draw
from src.services import social_arcade_ui as ui
from src.services.social_arcade_service import SocialArcadeService
from src.tests.test_social_arcade_service import Repository
from src.tests.test_social_arcade_handler import Bot, _group_callback
from src.handlers.social_arcade_handler import SocialArcadeHandler
from src.handlers.puzzle_suite import PuzzleSuite
from src.games.puzzle_catalog import PUZZLES, TEXT_PUZZLE_KEYS
from src.games.help_catalog import GAME_HELP
from src.services.menu_service import MenuService


def started(seed=7):
    state = draw.new(game_key="ciz_bil", chat_id=-100, thread_id=9,
                     owner_id=1, owner_name="Çizer", round_id="draw-round", now=100)
    state = draw.join(state, user_id=2, user_name="Tahminci", now=101)
    state = draw.ready(state, user_id=1)
    state = draw.ready(state, user_id=2)
    return draw.start(state, user_id=1, now=102, seed=seed)


def live(state, now=103):
    state["phase_ready"] = True
    state["group_ready"] = True
    state["deadline"] = now + 120
    state["panel_wait_until"] = None
    return state


def test_drawer_receives_a_concrete_private_word_while_group_never_sees_it():
    state = started()
    assert state["phase"] == "draw" and state["private_recipients"] == [1]
    assert state["artist_id"] == 1
    word = state["word"]
    assert word in draw.DRAWABLE_WORDS
    assert word not in str(draw.public_view(state))
    assert word in ui.private_text(state, 1)
    assert word not in ui.group_text(state)


def test_only_drawer_can_submit_a_fresh_photo_then_other_players_guess():
    state = live(started())
    with pytest.raises(ValueError, match="sa_not_drawer"):
        draw.act(state, user_id=2, action="drawing", value="file-id", now=104)
    state = draw.act(state, user_id=1, action="drawing", value="file-id", now=104)
    assert state["phase"] == "guess"
    assert state["drawing_file_id"] == "file-id"
    assert "file-id" not in str(draw.public_view(state))
    state["phase_ready"], state["deadline"] = True, 200
    state["drawing_message_id"], state["drawing_pending"] = 44, False
    with pytest.raises(ValueError, match="sa_not_guesser"):
        draw.act(state, user_id=1, action="draw_guess", text=state["word"], now=105)
    result = draw.act(state, user_id=2, action="draw_guess", text=state["word"], now=105)
    assert result["phase"] == "result"
    assert result["scores"]["2"] > 0 and result["scores"]["1"] > 0
    assert result["last_event"]["word"] == state["word"]


def test_turkish_words_accept_plain_keyboard_spelling():
    state = live(started())
    state["word"] = "şemsiye"
    state = draw.act(state, user_id=1, action="drawing", value="file-id", now=104)
    state["phase_ready"], state["deadline"] = True, 200
    state["drawing_message_id"], state["drawing_pending"] = 44, False
    result = draw.act(state, user_id=2, action="draw_guess", text="SEMSIYE", now=105)
    assert result["phase"] == "result"


def test_draw_match_rotates_artist_and_finishes_without_duplicate_words():
    state = live(started())
    first_word = state["word"]
    state = draw.act(state, user_id=1, action="drawing", value="first-file", now=104)
    state["phase_ready"], state["deadline"] = True, 200
    state["drawing_message_id"], state["drawing_pending"] = 44, False
    state = draw.act(state, user_id=2, action="draw_guess", text=first_word, now=105)
    state["phase_ready"], state["deadline"] = True, 106
    state = draw.advance(state, now=107)
    assert state["phase"] == "draw" and state["artist_index"] == 1
    assert state["artist_id"] == 2
    assert state["word"] != first_word
    state = live(state, now=108)
    state = draw.act(state, user_id=2, action="drawing", value="second-file", now=109)
    state["phase_ready"], state["deadline"] = True, 200
    state["drawing_message_id"], state["drawing_pending"] = 45, False
    state = draw.act(state, user_id=1, action="draw_guess", text=state["word"], now=110)
    state["phase_ready"], state["deadline"] = True, 111
    state = draw.advance(state, now=112)
    assert state["phase"] == "finished" and set(state["winner_ids"]) <= {1, 2}


def test_guess_budget_and_deadline_prevent_spam_or_late_scoring():
    state = live(started())
    state = draw.act(state, user_id=1, action="drawing", value="file", now=104)
    state["phase_ready"], state["deadline"] = True, 200
    state["drawing_message_id"], state["drawing_pending"] = 44, False
    for message in ("yanlış bir", "yanlış iki"):
        state = draw.act(state, user_id=2, action="draw_guess", text=message, now=105)
    with pytest.raises(ValueError, match="sa_guess_limit"):
        draw.act(state, user_id=2, action="draw_guess", text="yanlış üç", now=105)
    with pytest.raises(ValueError, match="sa_wrong_phase"):
        draw.act(state, user_id=2, action="draw_guess", text=state["word"], now=201)


@pytest.mark.asyncio
async def test_drawing_delivery_is_claimed_and_acknowledged_before_guess_timer():
    state = live(started())
    state = draw.act(state, user_id=1, action="drawing", value="photo-file-id", now=104)
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    service = SocialArcadeService(repo)
    claimed = await service.claim_delivery(game_key="ciz_bil", chat_id=-100,
                                           thread_id=9, kind="drawing", now=105)
    assert claimed["drawing_file_id"] == "photo-file-id"
    assert claimed["drawing_pending"] is True
    await service.acknowledge_drawing(game_key="ciz_bil", chat_id=-100, thread_id=9,
        round_id=state["round_id"], phase_seq=state["phase_seq"],
        message_id=300, now=106)
    current = await service.current(game_key="ciz_bil", chat_id=-100, thread_id=9)
    assert current["drawing_message_id"] == 300
    assert current["drawing_pending"] is False and current["panel_dirty"] is True


@pytest.mark.asyncio
async def test_private_artist_photo_reaches_group_without_revealing_word():
    moment = time.time()
    state = draw.new(game_key="ciz_bil", chat_id=-100, thread_id=9,
                     owner_id=1, owner_name="Çizer", round_id="draw", now=moment)
    state = draw.join(state, user_id=2, user_name="Tahminci", now=moment)
    state = draw.ready(state, user_id=1)
    state = draw.ready(state, user_id=2)
    state = draw.start(state, user_id=1, now=moment, seed=4)
    state.update(phase_ready=True, group_ready=True, deadline=moment + 120,
                 panel_wait_until=None, panel_message_id=70,
                 private_panels={"1": {"phase_seq": state["phase_seq"],
                                       "message_id": 10, "opened_at": moment}},
                 private_delivery={"1": 10})
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    service = SocialArcadeService(repo)
    handler = SocialArcadeHandler(service)
    message = SimpleNamespace(message_id=11, caption=None,
        photo=[SimpleNamespace(file_id="photo-file-id", file_size=1000)],
        reply_to_message=SimpleNamespace(message_id=10),
        date=datetime.fromtimestamp(moment + 1, timezone.utc), reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(id=1, first_name="Çizer"), effective_message=message)
    bot = SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=71)),
                          send_message=AsyncMock(), edit_message_text=AsyncMock())
    context = SimpleNamespace(bot=bot)
    assert await handler.on_private_photo(update, context,
        routes=[{"game_type": "ciz_bil_t9", "chat_id": -100}])
    current = await service.current(game_key="ciz_bil", chat_id=-100, thread_id=9)
    assert current["phase"] == "guess" and current["drawing_message_id"] == 71
    assert bot.send_photo.await_args.kwargs["chat_id"] == -100
    assert current["word"] not in str(bot.send_photo.await_args.kwargs)


@pytest.mark.asyncio
async def test_caption_containing_secret_is_rejected_before_private_photo_submission():
    state = started()
    state["private_panels"] = {"1": {"phase_seq": state["phase_seq"],
                                      "message_id": 10, "opened_at": 102}}
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    handler = SocialArcadeHandler(SocialArcadeService(repo))
    message = SimpleNamespace(message_id=11, caption=state["word"],
        photo=[SimpleNamespace(file_id="photo-file-id", file_size=1000)],
        reply_to_message=None, date=datetime.now(timezone.utc), reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(id=1, first_name="Çizer"), effective_message=message)
    assert await handler.on_private_photo(update, SimpleNamespace(bot=SimpleNamespace()),
        routes=[{"game_type": "ciz_bil_t9", "chat_id": -100}])
    message.reply_text.assert_awaited_once()
    assert (await handler.service.current(game_key="ciz_bil", chat_id=-100,
                                          thread_id=9))["phase"] == "draw"


@pytest.mark.asyncio
async def test_private_photo_routes_only_to_the_drawing_adapter():
    class Repository:
        async def list_active_draw_routes(self, user_id):
            assert user_id == 1
            return [{"chat_id": -100, "game_type": "ciz_bil_t9"}]
    class Adapter:
        def __init__(self):
            self.calls = []
        async def on_private_photo(self, update, context, *, routes):
            self.calls.append(routes)
            return True
    adapter = Adapter()
    suite = PuzzleSuite({"ciz_bil": adapter}, repository=Repository())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1))
    assert await suite.on_private_photo(update, SimpleNamespace()) is True
    assert adapter.calls == [[{"chat_id": -100, "game_type": "ciz_bil_t9"}]]


@pytest.mark.asyncio
@pytest.mark.parametrize("captioned", [False, True])
async def test_orchestrator_moderates_then_routes_artist_photo_before_media_ai(captioned):
    from src.orchestrator import Orchestrator

    app = Orchestrator.__new__(Orchestrator)
    app.media_handler = SimpleNamespace(moderate_update=AsyncMock(return_value=False),
        handle_photo=AsyncMock(), handle_document=AsyncMock())
    app.puzzle_suite = SimpleNamespace(on_private_photo=AsyncMock(return_value=True))
    message = SimpleNamespace(photo=[SimpleNamespace(file_id="file")],
                              caption="" if captioned else None)
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="private"),
                             effective_message=message)
    if captioned:
        await app.handle_caption(update, SimpleNamespace())
    else:
        await app.handle_photo(update, SimpleNamespace())
    app.media_handler.moderate_update.assert_awaited_once()
    app.puzzle_suite.on_private_photo.assert_awaited_once()
    app.media_handler.handle_photo.assert_not_awaited()


def test_draw_guess_is_discoverable_and_group_text_routed():
    entry = next(p for p in PUZZLES if p.key == "ciz_bil")
    assert entry.command == "cizbil" and entry.min_players == 2
    assert "ciz_bil" in TEXT_PUZZLE_KEYS and "ciz_bil" in GAME_HELP
    labels = [button.callback_data for row in MenuService().games_menu(category="social")[1].inline_keyboard
              for button in row]
    assert "game_start_ciz_bil" in labels


@pytest.mark.asyncio
async def test_normal_group_guess_scores_only_after_drawing_arrives():
    moment = time.time()
    state = draw.new(game_key="ciz_bil", chat_id=-100, thread_id=9,
                     owner_id=1, owner_name="Çizer", round_id="draw", now=moment)
    state = draw.join(state, user_id=2, user_name="Tahminci", now=moment)
    state = draw.ready(state, user_id=1)
    state = draw.ready(state, user_id=2)
    state = draw.start(state, user_id=1, now=moment, seed=4)
    state = live(state, now=moment)
    state = draw.act(state, user_id=1, action="drawing", value="file", now=moment)
    state.update(phase_ready=True, group_ready=True, deadline=moment + 90,
                 drawing_message_id=71, drawing_pending=False, panel_message_id=70,
                 panel_wait_until=None)
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    handler = SocialArcadeHandler(SocialArcadeService(repo))
    message = SimpleNamespace(message_id=72, text=state["word"],
        message_thread_id=9, is_topic_message=True,
        date=datetime.fromtimestamp(moment + 1, timezone.utc))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=2, first_name="Tahminci"), effective_message=message,
        edited_message=None)
    context = SimpleNamespace(bot=SimpleNamespace(edit_message_text=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=73)),
        edit_message_reply_markup=AsyncMock()))
    assert await handler.on_text(update, context, state["word"])
    current = await handler.service.current(game_key="ciz_bil", chat_id=-100, thread_id=9)
    assert current["phase"] == "result" and current["scores"]["2"] > 0


@pytest.mark.asyncio
async def test_scored_guess_survives_a_temporary_group_panel_failure():
    moment = time.time()
    state = draw.new(game_key="ciz_bil", chat_id=-100, thread_id=9,
                     owner_id=1, owner_name="Çizer", round_id="draw", now=moment)
    state = draw.join(state, user_id=2, user_name="Tahminci", now=moment)
    state = draw.ready(state, user_id=1)
    state = draw.ready(state, user_id=2)
    state = draw.start(state, user_id=1, now=moment, seed=4)
    state = live(state, now=moment)
    state = draw.act(state, user_id=1, action="drawing", value="file", now=moment)
    state.update(phase_ready=True, deadline=moment + 90, drawing_message_id=71,
                 drawing_pending=False, panel_message_id=70, panel_wait_until=None)
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    handler = SocialArcadeHandler(SocialArcadeService(repo))
    handler._flush = AsyncMock(side_effect=RuntimeError("telegram down"))
    message = SimpleNamespace(message_id=72, is_topic_message=True,
        message_thread_id=9, date=datetime.fromtimestamp(moment + 1, timezone.utc))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=2, first_name="Tahminci"),
        effective_message=message, edited_message=None)
    assert await handler.on_text(update, SimpleNamespace(bot=SimpleNamespace()), state["word"])
    saved = await handler.service.current(game_key="ciz_bil", chat_id=-100, thread_id=9)
    assert saved["phase"] == "result" and saved["panel_dirty"] is True


@pytest.mark.asyncio
async def test_draw_lobby_accepts_an_already_open_private_chat_without_start(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 101)
    service = SocialArcadeService(Repository())
    _, state = await service.open(game_key="ciz_bil", chat_id=-100,
        thread_id=8, user_id=1, user_name="Çizer", now=100)
    bot = Bot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"chat_id": -100, "state": state})
    current = await service.current(game_key="ciz_bil", chat_id=-100, thread_id=8)
    update, _ = _group_callback(current, 1, "ready")
    await handler.callback(update, SimpleNamespace(bot=bot))
    current = await service.current(game_key="ciz_bil", chat_id=-100, thread_id=8)
    assert current["players"][0]["ready"] is True
    assert current["dm_invites"] == {}


@pytest.mark.asyncio
async def test_guess_phase_creates_a_fresh_panel_below_the_group_drawing():
    moment = time.time()
    state = draw.new(game_key="ciz_bil", chat_id=-100, thread_id=9,
                     owner_id=1, owner_name="Çizer", round_id="draw", now=moment)
    state = draw.join(state, user_id=2, user_name="Tahminci", now=moment)
    state = draw.ready(state, user_id=1)
    state = draw.ready(state, user_id=2)
    state = draw.start(state, user_id=1, now=moment, seed=4)
    state = live(state, now=moment)
    state = draw.act(state, user_id=1, action="drawing", value="file", now=moment)
    state.update(drawing_message_id=71, drawing_pending=False,
                 panel_message_id=70, panel_phase_seq=state["phase_seq"] - 1)
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    service = SocialArcadeService(repo)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=72)),
                          edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock())
    await SocialArcadeHandler(service).publish(bot, game_key="ciz_bil", chat_id=-100,
                                               thread_id=9)
    current = await service.current(game_key="ciz_bil", chat_id=-100, thread_id=9)
    assert bot.send_message.await_count == 1
    assert bot.edit_message_text.await_count == 0
    assert current["panel_message_id"] == 72 and current["panel_phase_seq"] == current["phase_seq"]


@pytest.mark.asyncio
async def test_delayed_message_from_before_guess_panel_cannot_score():
    moment = time.time()
    state = draw.new(game_key="ciz_bil", chat_id=-100, thread_id=9,
                     owner_id=1, owner_name="Çizer", round_id="draw", now=moment)
    state = draw.join(state, user_id=2, user_name="Tahminci", now=moment)
    state = draw.ready(state, user_id=1)
    state = draw.ready(state, user_id=2)
    state = draw.start(state, user_id=1, now=moment, seed=4)
    state = live(state, now=moment)
    state = draw.act(state, user_id=1, action="drawing", value="file", now=moment)
    state.update(phase_ready=True, deadline=moment + 90,
                 drawing_message_id=71, drawing_pending=False,
                 panel_message_id=72, guess_started_at=moment + 2)
    repo = Repository()
    repo.rows[(-100, "ciz_bil_t9")] = state
    service = SocialArcadeService(repo)
    with pytest.raises(ValueError, match="sa_stale_input"):
        await service.act(game_key="ciz_bil", chat_id=-100, thread_id=9,
            user_id=2, action_id="old-msg", action="draw_guess", text=state["word"],
            round_id=state["round_id"], phase_seq=state["phase_seq"],
            input_message_id=71, submitted_at=moment + 1)
