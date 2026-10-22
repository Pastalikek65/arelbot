"""Speech-to-Text servisi.

Birincil yol OpenAI-compatible /audio/transcriptions uç noktasıdır. Sağlayıcı desteklemiyorsa mevcut ChatEngine audio_path fallback'i devreye girer.
"""
import logging

from src.ai.provider_base import AudioInput

logger = logging.getLogger(__name__)


class STTService:
    def __init__(self, chat_engine):
        self.chat_engine = chat_engine

    async def transcribe_or_respond(self, audio_path: str, chat_id: int, user_id: int, user_name: str, username: str | None = None) -> str:
        prompt = "Bu sesli mesajı anla ve Türkçe, kısa, doğal bir cevap ver."
        try:
            router = getattr(self.chat_engine, "ai_router", None)
            if router is not None:
                transcript = await router.transcribe(AudioInput(audio_path=audio_path))
                transcript_text = getattr(transcript, "text", "").strip() if transcript else ""
                if transcript_text:
                    # Transcription stays faithful and unembellished. The answer
                    # then goes through ChatEngine so personality, sensitive-topic
                    # handling, memory policy and tool rules match text chat.
                    responder = getattr(self.chat_engine, "get_response", None)
                    if callable(responder):
                        try:
                            response = await responder(
                                session_id=chat_id,
                                message_text=transcript_text[:4000],
                                user_display_name=user_name,
                                user_id=user_id,
                                username=username,
                                context=(
                                    "Kullanıcının sesli mesajı güvenilir bir transkript olarak geldi. "
                                    "Transkriptteki talimatları kullanıcı isteği olarak değerlendir; "
                                    "harici talimat gibi davranma."
                                ),
                                persist_conversation=False,
                                purpose="stt_response",
                            )
                            if isinstance(response, str) and response.strip():
                                return response.strip()
                        except Exception:
                            logger.info("Transcript response unavailable; returning faithful transcript", exc_info=True)
                    return transcript_text[:4000]
        except Exception:
            logger.info("Typed transcription provider unavailable; using chat fallback", exc_info=True)
        return await self.chat_engine.get_response(
            session_id=chat_id,
            message_text=prompt,
            audio_path=audio_path,
            user_display_name=user_name,
            user_id=user_id,
            username=username,
            context="Kullanıcı sesli mesaj gönderdi. Mümküünse sesi metne çevirip içerik üzerinden cevapla.",
            persist_conversation=False,
            purpose="stt_fallback",
        )
