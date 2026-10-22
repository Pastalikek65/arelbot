"""News digest orchestration over the typed feature/provider runtime."""

from __future__ import annotations

import html
from typing import Any, Iterable

from src.features.contracts import FeatureResult, ProviderRequest
from src.features.providers.rss_news import DEFAULT_FEEDS, RssNewsProvider
from src.features.runtime import ProviderRuntime
from src.security.output_sanitizer import sanitize_html


class _SharedCacheAdapter:
    """Bridge Redis clients and the project's ``ttl=`` cache wrappers."""

    def __init__(self, cache: Any) -> None:
        self.cache = cache

    async def get(self, key: str):
        return await self.cache.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None):
        try:
            return await self.cache.set(key, value, ex=ex)
        except TypeError:
            return await self.cache.set(key, value, ttl=ex)


class NewsDigestService:
    """Render a source-attributed digest without doing network I/O in handlers."""

    def __init__(
        self,
        chat_engine=None,
        cache=None,
        feeds: Iterable[str] | None = None,
        *,
        feature_executor=None,
        provider: RssNewsProvider | None = None,
    ):
        self.chat_engine = chat_engine
        self.feature_executor = feature_executor
        self.provider = provider or RssNewsProvider(feeds or DEFAULT_FEEDS)
        self.runtime = ProviderRuntime(
            self.provider,
            cache_ttl_seconds=600,
            stale_ttl_seconds=1800,
            shared_cache=_SharedCacheAdapter(cache) if cache is not None else None,
        )

    async def fetch_result(self, limit: int = 8) -> FeatureResult[dict[str, Any]]:
        limit = max(1, min(int(limit), 30))
        if self.feature_executor is not None:
            return await self.feature_executor.execute("news_digest", {"limit": limit})
        try:
            response = await self.runtime.fetch(
                ProviderRequest(operation="news_digest", params={"limit": limit}, timeout_seconds=8.0)
            )
            return FeatureResult.success(
                response.data,
                display_blocks=(response.data.get("content", ""),),
                response=response,
            )
        except ValueError as exc:
            return FeatureResult.failure(str(exc).upper(), "Haber sayısı isteğini kontrol edin.")
        except Exception:
            return FeatureResult.failure("NEWS_PROVIDER_UNAVAILABLE", "Haber akışı şu an alınamıyor.")

    async def fetch_items(self, limit: int = 8) -> list[dict[str, str]]:
        result = await self.fetch_result(limit)
        if not result.ok or not isinstance(result.data, dict):
            return []
        return list(result.data.get("items") or [])

    async def render_digest(self, chat_id: int | None = None) -> str:
        result = await self.fetch_result()
        items = list((result.data or {}).get("items") or []) if result.ok else []
        if not items:
            return "📰 Haber akışı şu an alınamıyor. Biraz sonra tekrar dene."

        raw = "\n".join(f"- {item.get('title', '')}" for item in items[:8])
        if self.chat_engine and chat_id:
            prompt = (
                "Aşağıdaki haber başlıklarını Türkçe, tarafsız ve beş maddelik kısa "
                "günlük özet haline getir. Kaynakta olmayan ayrıntı uydurma:\n" + raw
            )
            summary = await self.chat_engine.get_response(
                chat_id,
                prompt,
                user_display_name="Sistem",
                user_id=0,
                persist_conversation=False,
                purpose="news_digest_summary",
            )
            if summary:
                return f"📰 <b>AI HABER ÖZETİ</b>\n\n{sanitize_html(summary)}"

        lines = ["📰 <b>GÜNCEL HABER ÖZETİ</b>", ""]
        for index, item in enumerate(items[:5], 1):
            title = html.escape(str(item.get("title", "")))
            link = html.escape(str(item.get("link", "")), quote=True)
            lines.append(f'{index}. <a href="{link}">{title}</a>' if link else f"{index}. {title}")
        if result.stale:
            lines.append("\n<i>Kaynak geçici olarak kullanılamadı; önbellekteki son veri gösteriliyor.</i>")
        if result.source:
            source = html.escape(result.source)
            lines.append(f"<i>Kaynak: {source}</i>")
        if result.fetched_at:
            fetched = result.fetched_at.astimezone().strftime("%d.%m.%Y %H:%M")
            lines.append(f"<i>Veri zamanı: {html.escape(fetched)}</i>")
        return "\n".join(lines)

