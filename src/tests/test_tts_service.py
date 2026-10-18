from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.services.tts_service import SpeechArtifact, TTSService


class FakeSpeechProvider:
    name = "fake-speech"

    def __init__(self):
        self.requests = []
        self.active = 0
        self.max_active = 0

    async def synthesize(self, request):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            Path(request.output_path).write_bytes(b"ID3-fake-audio")
            return SpeechArtifact(path=request.output_path, mime_type="audio/mpeg", provider=self.name)
        finally:
            self.active -= 1

    async def health_check(self):
        return {"name": self.name, "healthy": True}


@pytest.mark.asyncio
async def test_tts_service_uses_typed_provider_and_atomically_publishes_audio(tmp_path):
    provider = FakeSpeechProvider()
    service = TTSService(provider=provider, output_dir=tmp_path, max_concurrency=1)

    output = await service.synthesize("<b>Merhaba</b> https://example.com", language="tr")

    assert Path(output).parent == tmp_path
    assert Path(output).read_bytes() == b"ID3-fake-audio"
    assert provider.requests[0].text == "Merhaba link"
    assert provider.requests[0].voice == "tr-TR-AhmetNeural"
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_tts_service_rejects_output_path_escape_and_limits_concurrency(tmp_path):
    provider = FakeSpeechProvider()
    service = TTSService(provider=provider, output_dir=tmp_path, max_concurrency=1)

    with pytest.raises(ValueError, match="tts_output_path_invalid"):
        await service.synthesize("test", output_path=tmp_path.parent / "outside.mp3")

    await asyncio.gather(
        service.synthesize("bir"),
        service.synthesize("iki"),
    )
    assert provider.max_active == 1


@pytest.mark.asyncio
async def test_tts_service_cleans_partial_file_when_provider_fails(tmp_path):
    class BrokenProvider(FakeSpeechProvider):
        async def synthesize(self, request):
            Path(request.output_path).write_bytes(b"partial")
            raise RuntimeError("upstream_down")

    service = TTSService(provider=BrokenProvider(), output_dir=tmp_path)

    with pytest.raises(RuntimeError, match="upstream_down"):
        await service.synthesize("test")

    assert not list(tmp_path.iterdir())

