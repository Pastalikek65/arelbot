"""Typed adapter for user-isolated document retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...rag_engine import RAGEngine
from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse


class DocumentSearchProvider:
    name = "document_search"
    capabilities = {"document_search", "user_scoped_retrieval"}

    def __init__(self, rag_engine: RAGEngine):
        self.rag_engine = rag_engine

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]:
        query = str(request.params.get("query") or "")
        user_id = request.params.get("user_id")
        content = await self.rag_engine.search(query, user_id=user_id)
        return ProviderResponse(
            data={"content": content},
            source="User-provided documents",
            source_url=None,
            fetched_at=datetime.now(timezone.utc),
            metadata={"untrusted": True, "user_scoped": True},
        )

    async def normalize(self, raw: object) -> object:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name,
            healthy=self.rag_engine.collection is not None,
            detail=None if self.rag_engine.collection is not None else "storage_unavailable",
        )
