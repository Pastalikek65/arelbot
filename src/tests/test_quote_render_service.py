from __future__ import annotations

import asyncio

import pytest

from src.quotes.render_service import QuoteRenderError, QuoteRenderService


class FakeManager:
    def __init__(self, *, payload: bytes = b"quote", delay: float = 0):
        self.payload = payload
        self.delay = delay

    def generate_quote(self, messages, output_path, **options):
        if self.delay:
            import time
            time.sleep(self.delay)
        with open(output_path, "wb") as handle:
            handle.write(self.payload)
        return output_path


@pytest.mark.asyncio
async def test_quote_render_service_publishes_bounded_artifact_atomically(tmp_path):
    service = QuoteRenderService(
        output_dir=tmp_path / "quotes",
        manager_factory=lambda: FakeManager(payload=b"valid"),
        max_messages=2,
        max_output_bytes=16,
    )

    artifact = await service.render([{"text": "Merhaba"}], {"is_png": False})

    assert artifact.path.parent == (tmp_path / "quotes").resolve()
    assert artifact.path.suffix == ".webp"
    assert artifact.size == 5
    assert artifact.path.read_bytes() == b"valid"
    assert not list((tmp_path / "quotes").glob("*.part"))


@pytest.mark.asyncio
async def test_quote_render_service_rejects_message_and_output_limits(tmp_path):
    service = QuoteRenderService(
        output_dir=tmp_path,
        manager_factory=lambda: FakeManager(payload=b"123456789"),
        max_messages=1,
        max_output_bytes=4,
    )

    with pytest.raises(QuoteRenderError, match="message_limit"):
        await service.render([{"text": "a"}, {"text": "b"}], {})
    with pytest.raises(QuoteRenderError, match="output_too_large"):
        await service.render([{"text": "a"}], {})


@pytest.mark.asyncio
async def test_quote_render_service_cleans_partial_output_on_timeout(tmp_path):
    service = QuoteRenderService(
        output_dir=tmp_path,
        manager_factory=lambda: FakeManager(delay=0.5),
        timeout_seconds=0.01,
    )

    with pytest.raises(QuoteRenderError, match="render_timeout"):
        await service.render([{"text": "a"}], {})
    await asyncio.sleep(0.02)
    assert not list(tmp_path.glob("*.part"))
