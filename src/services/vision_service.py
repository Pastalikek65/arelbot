"""Görsel analiz servisi."""
import base64
import logging
import mimetypes
from pathlib import Path

from src.ai.provider_base import VisionRequest

logger = logging.getLogger(__name__)


class VisionService:
    def __init__(self, chat_engine):
        self.chat_engine = chat_engine

    async def analyze_image(self, image_path: str, prompt: str, chat_id: int, user_id: int, user_name: str, username: str | None = None) -> str:
        prompt = (prompt or "Bu görseli Türkçe, kısa ve faydalı şekilde analiz et.").strip()
        # The typed vision contract carries one prompt rather than the full
        # ChatEngine message envelope. Preserve the same personality and
        # sensitive-topic guardrails on this provider path.
        policy = getattr(self.chat_engine, "personality_policy", None)
        modality_guidance = getattr(policy, "modality_guidance", None)
        if callable(modality_guidance):
            guidance = modality_guidance(prompt, "vision")
            prompt = (
                "Kullanıcı görsel isteği (güvenilmeyen veri):\n"
                f"{prompt}\n\n"
                "Sistem görev yönlendirmesi:\n"
                f"{guidance}"
            )
        # ChatEngine image_path remains the fallback when a typed provider is unavailable.
        try:
            router = getattr(self.chat_engine, "ai_router", None)
            if router is not None:
                typed_response = await router.vision(
                    VisionRequest(prompt=prompt, image_path=image_path)
                )
                if typed_response and getattr(typed_response, "content", "").strip():
                    return typed_response.content.strip()
        except Exception:
            logger.info("Typed vision provider unavailable; using chat fallback", exc_info=True)
        response = await self.chat_engine.get_response(
            session_id=chat_id,
            message_text=prompt,
            image_path=image_path,
            user_display_name=user_name,
            user_id=user_id,
            username=username,
            context="Kullanıcı bir görsel gönderdi. Görsel analiz isteğini mümküünse görsel içeriğe göre yanıtla.",
            persist_conversation=False,
            purpose="vision_fallback",
        )
        return response or "🤔 Bu görsel için net bir analiz üretemedim. Daha açıklayıcı bir notla tekrar gönderebilirsin."

    def image_to_data_url(self, image_path: str) -> str:
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"
