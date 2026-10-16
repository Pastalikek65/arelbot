from datetime import datetime, timedelta, timezone

import pytest

from src.services.economy_service import EconomyService
from src.handlers.command_handler import CommandHandler
from src.handlers.callback_handler import CallbackHandler
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_economy_credit_is_idempotent_and_debit_is_bounded():
    service = EconomyService(db=None)

    assert await service.credit(1, -100, 50, reason="quiz", event_id="quiz:1") is True
    assert await service.credit(1, -100, 50, reason="quiz", event_id="quiz:1") is False
    assert await service.balance(1, -100) == 50
    assert await service.debit(1, -100, 75, reason="shop", event_id="shop:1") is False
    assert await service.balance(1, -100) == 50


@pytest.mark.asyncio
async def test_economy_daily_reward_has_cooldown_and_group_scope():
    service = EconomyService(db=None)

    first = await service.claim_daily(7, -100, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    second = await service.claim_daily(7, -100, now=datetime(2026, 10, 5, 12, tzinfo=timezone.utc))
    next_day = await service.claim_daily(7, -100, now=datetime(2026, 10, 6, tzinfo=timezone.utc))

    assert first["accepted"] is True
    assert second["accepted"] is False
    assert next_day["accepted"] is True
    assert await service.balance(7, -100) == first["amount"] + next_day["amount"]
    assert await service.balance(7, -101) == 0


@pytest.mark.asyncio
async def test_economy_rejects_invalid_amounts():
    service = EconomyService(db=None)
    with pytest.raises(ValueError, match="amount_out_of_range"):
        await service.credit(1, 1, 0, reason="bad")
    with pytest.raises(ValueError, match="amount_out_of_range"):
        await service.credit(1, 1, 10001, reason="bad")


@pytest.mark.asyncio
async def test_economy_commands_render_user_scoped_balance():
    service = EconomyService(db=None)
    await service.credit(3, -100, 40, reason="quiz", event_id="quiz:1")
    sent = []

    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append(text)

    update = type("U", (), {
        "effective_user": type("User", (), {"id": 3})(),
        "effective_chat": type("Chat", (), {"id": -100})(),
        "effective_message": Message(),
    })()
    handler = CommandHandler(None, None, None, None, economy_service=service)
    await handler.handle_economy_command(update, None)

    assert sent and "40" in sent[0]


@pytest.mark.asyncio
async def test_quiz_reward_is_idempotent_with_score_event():
    class DB:
        async def get_game_state(self, chat_id, game_type):
            return None

        async def delete_game_state(self, chat_id, game_type):
            return None

    class Leaderboard:
        async def record_score(self, *args, **kwargs):
            return True

    economy = EconomyService(db=None)
    handler = CallbackHandler(
        type("Engine", (), {"db": DB()})(),
        None,
        None,
        leaderboard_service=Leaderboard(),
        economy_service=economy,
    )
    query = type("Query", (), {
        "message": type("M", (), {"message_id": 44})(),
        "edit_message_text": AsyncMock(),
        "answer": AsyncMock(),
    })()
    update = type("U", (), {
        "effective_user": type("User", (), {"id": 7, "first_name": "Arel"})(),
        "effective_chat": type("Chat", (), {"id": -100})(),
    })()
    context = type("C", (), {"chat_data": {"quiz_answer": "Ankara", "quiz_question": "BaÅŸkent?"}})()

    await handler._handle_quiz_answer(update, query, context, "quiz_ans_0_Ankara")
    assert await economy.balance(7, -100) == 5
