from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.embedding_service import EmbeddingService
from src.rag_engine import RAGEngine


@pytest.mark.asyncio
async def test_embedding_service_caches_normalized_text():
    router = SimpleNamespace(embed=AsyncMock(return_value=[[0.1, 0.2, 0.3]]))
    service = EmbeddingService(router, enabled=True, max_cache_entries=16)

    first = await service.embed_one("  Merhaba  ")
    second = await service.embed_one("Merhaba")

    assert first == second == [0.1, 0.2, 0.3]
    router.embed.assert_awaited_once_with(["Merhaba"], provider_names=None)
    assert (await service.stats())["entries"] == 1


@pytest.mark.asyncio
async def test_embedding_service_is_disabled_without_external_calls():
    router = SimpleNamespace(embed=AsyncMock())
    service = EmbeddingService(router, enabled=False)

    assert await service.embed_one("test") is None
    router.embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_service_rejects_invalid_vectors():
    router = SimpleNamespace(embed=AsyncMock(return_value=[[float("nan")]]))
    service = EmbeddingService(router, enabled=True)

    with pytest.raises(ValueError, match="VECTOR_INVALID"):
        await service.embed_one("test")


@pytest.mark.asyncio
async def test_rag_provider_mode_uses_external_vector_space():
    service = SimpleNamespace(embed_one=AsyncMock(return_value=[0.4, 0.5]))
    engine = RAGEngine.__new__(RAGEngine)
    engine.use_provider_embeddings = True
    engine.embedding_service = service

    assert await engine._resolve_embedding("belge") == [0.4, 0.5]
    service.embed_one.assert_awaited_once_with("belge")
