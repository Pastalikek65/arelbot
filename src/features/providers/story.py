"""Typed story-generation request provider."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse


class StoryProvider:
    name = "generate_story"
    capabilities = {"story", "creative_writing"}
    styles = {"masal", "gerilim", "komik", "duygusal", "bilimkurgu", "macera", "gizem"}

    @staticmethod
    def _value(request: ProviderRequest, key: str, default: str = "") -> str:
        return str(request.params.get(key) or default).strip()[:240]

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        topic = self._value(request, "topic")
        style = self._value(request, "style", "masal").casefold()
        if len(topic) < 2:
            raise ValueError("story_topic_required")
        if style not in self.styles:
            raise ValueError("story_style_invalid")
        safe_topic = html.escape(topic)
        safe_style = html.escape(style)
        content = (
            "<b>\U0001f4d6 Hik\u00e2ye olu\u015fturma</b>\n\n"
            f"Konu: {safe_topic}\n"
            f"Tarz: {safe_style}\n\n"
            "Bu istek yarat\u0131c\u0131 AI katman\u0131na g\u00fcvenli bir \u00fcretim g\u00f6revi olarak iletildi. "
            "3\u20135 paragraf; giri\u015f, geli\u015fme ve sonu\u00e7 yap\u0131s\u0131yla haz\u0131rlanacak."
        )
        brief = {
            "topic": topic,
            "style": style,
            "generation_policy": "creative_story_3_to_5_paragraphs",
            "content": content,
        }
        return ProviderResponse(
            data=brief,
            source="Arelbot AI story provider",
            source_url=None,
            fetched_at=datetime.now(timezone.utc),
            metadata={
                "provider_kind": "typed_generation_brief",
                "external_data": False,
                "prompt_injection_safe": True,
            },
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="generation_brief_ready")
