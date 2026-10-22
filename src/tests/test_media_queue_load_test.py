from __future__ import annotations

import asyncio
import json

import pytest

from scripts.media_queue_load_test import _safe_report, run_load


@pytest.mark.asyncio
async def test_media_queue_load_harness_uses_production_queue_and_isolates_chats():
    result = await run_load(jobs=20, workers=4, chats=20)
    assert result["ok"] is True
    assert result["completed"] == 20
    assert result["active_chats"] == 20
    assert result["max_active_workers"] <= 4
    assert result["queue_depth"] == 0
    assert result["secret_free"] is True


def test_media_queue_load_report_is_atomic_and_secret_free(tmp_path):
    report = tmp_path / "evidence" / "queue.json"
    _safe_report(report, {"ok": True, "secret_free": True})
    assert json.loads(report.read_text(encoding="utf-8"))["secret_free"] is True
    assert not list(report.parent.glob("*.tmp"))


def test_media_queue_load_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        asyncio.run(run_load(jobs=0, workers=1, chats=1))
