from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from src.stickers.render_service import StickerRenderError, StickerRenderService


def _png_bytes(size=(900, 600)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (20, 40, 80, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_static_sticker_render_fits_telegram_canvas_and_returns_webp(tmp_path):
    service = StickerRenderService(temp_dir=tmp_path, max_concurrency=1)

    artifact = await service.render(_png_bytes(), kind="static")

    assert artifact.format == "webp"
    assert artifact.filename == "sticker.webp"
    assert len(artifact.content) > 0
    with Image.open(io.BytesIO(artifact.content)) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= 512
        assert image.mode == "RGBA"


@pytest.mark.asyncio
async def test_sticker_render_rejects_invalid_kind_and_oversized_input(tmp_path):
    service = StickerRenderService(temp_dir=tmp_path, max_input_bytes=10)

    with pytest.raises(StickerRenderError, match="sticker_kind_invalid"):
        await service.render(b"123", kind="audio")
    with pytest.raises(StickerRenderError, match="sticker_input_too_large"):
        await service.render(b"12345678901", kind="static")


@pytest.mark.asyncio
async def test_sticker_render_static_work_is_bounded(tmp_path):
    service = StickerRenderService(temp_dir=tmp_path, max_concurrency=1)
    active = 0
    peak = 0
    original = service._render_static

    def wrapped(content):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            return original(content)
        finally:
            active -= 1

    service._render_static = wrapped
    await asyncio.gather(
        service.render(_png_bytes((200, 200)), kind="static"),
        service.render(_png_bytes((200, 200)), kind="static"),
    )

    assert peak == 1

