from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.personality import PersonalityPolicy
from src.ai.provider_base import CompletionResponse, Transcription
from src.services.stt_service import STTService
from src.services.vision_service import VisionService


@pytest.mark.asyncio
async def test_vision_service_prefers_typed_router():
    router = SimpleNamespace(
        vision=AsyncMock(return_value=CompletionResponse("Görsel yanıtı", "demo", "vision"))
    )
    engine = SimpleNamespace(
        ai_router=router,
        get_response=AsyncMock(),
        personality_policy=PersonalityPolicy(sarcasm_level=1),
    )
    service = VisionService(engine)

    result = await service.analyze_image("image.jpg", "Ne var?", 1, 2, "Arel")

    assert result == "Görsel yanıtı"
    engine.get_response.assert_not_awaited()
    router.vision.assert_awaited_once()
    request = router.vision.await_args.args[0]
    assert "Sistem görev yönlendirmesi" in request.prompt
    assert "görsel" in request.prompt.casefold()


@pytest.mark.asyncio
async def test_stt_service_uses_chat_fallback_when_typed_provider_fails():
    router = SimpleNamespace(transcribe=AsyncMock(side_effect=RuntimeError("unsupported")))
    engine = SimpleNamespace(
        ai_router=router,
        get_response=AsyncMock(return_value="Ses yanıtı"),
    )
    service = STTService(engine)

    result = await service.transcribe_or_respond("audio.ogg", 1, 2, "Arel")

    assert result == "Ses yanıtı"
    engine.get_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_stt_service_routes_transcript_through_chat_completion():
    router = SimpleNamespace(
        transcribe=AsyncMock(return_value=Transcription("Merhaba", "demo", "whisper"))
    )
    engine = SimpleNamespace(
        ai_router=router,
        get_response=AsyncMock(return_value="Arel yanıtı"),
    )
    service = STTService(engine)

    result = await service.transcribe_or_respond("audio.ogg", 1, 2, "Arel")

    assert result == "Arel yanıtı"
    engine.get_response.assert_awaited_once()
    assert engine.get_response.await_args.kwargs["message_text"] == "Merhaba"


@pytest.mark.asyncio
async def test_stt_service_returns_transcript_when_response_engine_unavailable():
    router = SimpleNamespace(
        transcribe=AsyncMock(return_value=Transcription("Merhaba", "demo", "whisper"))
    )
    engine = SimpleNamespace(ai_router=router)
    service = STTService(engine)

    result = await service.transcribe_or_respond("audio.ogg", 1, 2, "Arel")

    assert result == "Merhaba"


@pytest.mark.asyncio
async def test_vision_service_marks_sensitive_prompt_for_serious_tone():
    router = SimpleNamespace(
        vision=AsyncMock(return_value=CompletionResponse("Görsel yanıtı", "demo", "vision"))
    )
    engine = SimpleNamespace(
        ai_router=router,
        get_response=AsyncMock(),
        personality_policy=PersonalityPolicy(sarcasm_level=2),
    )
    service = VisionService(engine)

    await service.analyze_image("image.jpg", "Deprem hasarını ve acil riskleri değerlendir", 1, 2, "Arel")

    request = router.vision.await_args.args[0]
    assert "kinaye" in request.prompt
    assert "ciddi" in request.prompt
