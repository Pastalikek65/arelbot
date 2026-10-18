import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.media.transcoder import MediaTranscodeError, MediaTranscoder


class HungProcess:
    def __init__(self):
        self.returncode = None
        self.terminate = Mock(side_effect=self.stopped)
        self.kill = Mock(side_effect=self.stopped)
        self.exited = asyncio.Event()
        self.entered = asyncio.Event()

    def stopped(self):
        self.returncode = -15
        self.exited.set()

    async def wait(self):
        self.entered.set()
        await self.exited.wait()
        return self.returncode


@pytest.mark.asyncio
async def test_conversion_timeout_reaps_child_and_removes_partial_output(tmp_path, monkeypatch):
    source = tmp_path / "video.webm"
    source.write_bytes(b"fixture")
    target = source.with_suffix(".telegram.mp4")
    target.write_bytes(b"partial")
    process = HungProcess()
    monkeypatch.setattr("src.media.transcoder.shutil.which", lambda name: "/fixture/ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    transcoder = MediaTranscoder(1024, timeout_seconds=0.01)
    with pytest.raises(MediaTranscodeError) as error:
        await transcoder.prepare({"path": str(source), "size": source.stat().st_size})
    assert error.value.code == "transcode_timeout"
    process.terminate.assert_called_once()
    assert process.returncode is not None
    assert not target.exists()
    assert source.exists()


@pytest.mark.asyncio
async def test_cancelled_conversion_stops_child_and_cleans_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "video.webm"
    source.write_bytes(b"fixture")
    target = source.with_suffix(".telegram.mp4")
    target.write_bytes(b"partial")
    process = HungProcess()
    monkeypatch.setattr("src.media.transcoder.shutil.which", lambda name: "/fixture/ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(MediaTranscoder(1024).prepare({"path": str(source)}))
    await asyncio.wait_for(process.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once()
    assert not target.exists()


@pytest.mark.asyncio
async def test_failed_conversion_does_not_send_original_incompatible_format(tmp_path, monkeypatch):
    source = tmp_path / "video.webm"
    source.write_bytes(b"fixture")
    process = SimpleFailedProcess()
    monkeypatch.setattr("src.media.transcoder.shutil.which", lambda name: "/fixture/ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(MediaTranscodeError) as error:
        await MediaTranscoder(1024).prepare({"path": str(source)})
    assert error.value.code == "transcode_failed"


class SimpleFailedProcess:
    returncode = 1

    async def wait(self):
        return 1


@pytest.mark.asyncio
async def test_successful_conversion_keeps_the_deliverable_file(tmp_path, monkeypatch):
    source = tmp_path / "video.webm"
    source.write_bytes(b"source")
    target = source.with_suffix(".telegram.mp4")

    class SuccessfulProcess:
        returncode = 0

        async def wait(self):
            target.write_bytes(b"converted-video")
            return 0

    monkeypatch.setattr("src.media.transcoder.shutil.which", lambda name: "/fixture/ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=SuccessfulProcess()))
    result = await MediaTranscoder(1024).prepare({"path": str(source)})
    assert result["extension"] == ".mp4"
    assert target.read_bytes() == b"converted-video"
    assert not source.exists()
