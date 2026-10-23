from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.message_handler import MessageHandler


@pytest.mark.asyncio
async def test_explicit_image_request_bypasses_llm_and_conversation_planner():
    handler = MessageHandler.__new__(MessageHandler)
    handler.process_image_request = AsyncMock()
    handler._process_group_targeted_replies = AsyncMock(return_value=True)
    handler.chat_engine = SimpleNamespace(get_response=AsyncMock())
    handler._log_event = AsyncMock()
    update = SimpleNamespace(effective_user=SimpleNamespace(full_name="Fixture", username="fixture", id=1),
                             effective_chat=SimpleNamespace(type="supergroup"))
    await handler.process_chat_request(update, -100, "Arel bana bir trex görseli oluştur", SimpleNamespace())
    handler.process_image_request.assert_awaited_once()
    assert "trex" in handler.process_image_request.await_args.args[1]
    handler._process_group_targeted_replies.assert_not_awaited()
    handler.chat_engine.get_response.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "Arel bana bir trex görseli oluştur",
    "@ArelChat_Bot mavi bir kedi resmi çizer misin",
    "Bir uzay fotoğrafı üretebilir misin?",
    "Bana bir çizim oluşturur musun",
])
def test_direct_turkish_image_requests(text):
    from src.image_generation.intent import explicit_image_prompt
    assert explicit_image_prompt(text)


@pytest.mark.parametrize("text", [
    "görsel oluşturma", "bana resim üretme", "görsel istemiyorum",
    "görsel üretimi nasıl çalışıyor", "görsel oluştur komutunu anlat",
    "biri bana görsel oluştur dedi", "görsel oluştur ama istemiyorum",
    "buz gibi çay da bir sanat", "/image bir kedi",
    "görsel oluştur kodu nasıl yazılır", "\"görsel oluştur\" yaz",
    "görsel oluştur cümlesini çevir", "", "x" * 4001,
])
def test_discussion_negation_and_normal_chat_do_not_create_images(text):
    from src.image_generation.intent import explicit_image_prompt
    assert explicit_image_prompt(text) is None
