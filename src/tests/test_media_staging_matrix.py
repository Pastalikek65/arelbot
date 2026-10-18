from __future__ import annotations

import json

import pytest

from scripts.media_staging_matrix import (
    MatrixError,
    _safe_report,
    load_matrix,
    run_live,
    validate_live_gate,
    validate_platform_matrix,
    validate_platform_waiver,
)


def test_media_matrix_validation_normalizes_enabled_and_disabled_cases(tmp_path):
    config = tmp_path / "matrix.json"
    config.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "youtube_case", "url": "https://www.youtube.com/shorts/fixture", "platform": "youtube"},
                    {"id": "instagram_case", "url": "", "platform": "instagram", "enabled": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    cases = load_matrix(config)
    assert cases[0]["platform"] == "youtube"
    assert cases[1] == {"id": "instagram_case", "enabled": False}


def test_media_matrix_rejects_platform_mismatch(tmp_path):
    config = tmp_path / "matrix.json"
    config.write_text(
        json.dumps({"cases": [{"id": "wrong_case", "url": "https://www.youtube.com/watch?v=x", "platform": "tiktok"}]}),
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="matrix_platform_mismatch"):
        load_matrix(config)


def test_media_matrix_can_require_the_complete_live_platform_set():
    cases = [
        {"id": "instagram", "platform": "instagram", "enabled": True},
        {"id": "tiktok", "platform": "tiktok", "enabled": True},
        {"id": "youtube", "platform": "youtube", "enabled": True},
        {"id": "x", "platform": "x", "enabled": True},
    ]
    validate_platform_matrix(cases, required=True)

    with pytest.raises(MatrixError, match="live_platform_matrix_incomplete"):
        validate_platform_matrix(cases[:3], required=True)

    validate_platform_matrix(cases[:1], required=False)


def test_media_matrix_owner_waiver_allows_one_unavailable_platform():
    cases = [
        {"id": "tiktok", "platform": "tiktok", "enabled": True},
        {"id": "youtube", "platform": "youtube", "enabled": True},
        {"id": "x", "platform": "x", "enabled": True},
    ]
    waived = validate_platform_waiver(
        ["instagram"],
        approver="owner",
        reason="source access restricted",
    )
    validate_platform_matrix(cases, required=True, waived_platforms=waived)

    with pytest.raises(MatrixError, match="live_platform_waiver_reason_invalid"):
        validate_platform_waiver(["instagram"], approver="owner", reason="bad\nreason")

    with pytest.raises(MatrixError, match="live_platform_matrix_incomplete"):
        validate_platform_matrix(cases[:2], required=True, waived_platforms=waived)


@pytest.mark.parametrize(
    ("live", "confirm", "env"),
    [(False, False, "1"), (True, False, "1"), (True, True, "0")],
)
def test_media_matrix_requires_redundant_live_gate(live, confirm, env):
    with pytest.raises(MatrixError, match="live_gate_required"):
        validate_live_gate(live=live, confirm_live=confirm, env_value=env)


def test_media_matrix_report_has_no_url_or_temporary_file(tmp_path):
    report = tmp_path / "reports" / "media.json"
    _safe_report(report, {"ok": True, "source_host": "www.youtube.com"})
    assert json.loads(report.read_text(encoding="utf-8"))["source_host"] == "www.youtube.com"
    assert not list(report.parent.glob("*.tmp"))


def test_media_matrix_small_sample_p95_uses_slowest_case(monkeypatch):
    async def fake_case(_provider, case, _semaphore, download, **kwargs):
        state = kwargs["execution_state"]
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        import asyncio
        await asyncio.sleep(0)
        state["active"] -= 1
        return {
            "case_id": case["id"],
            "run_index": kwargs.get("run_index", 0),
            "platform": case["platform"],
            "source_host": "example.test",
            "ok": True,
            "mode": "download" if download else "probe",
            "error_code": None,
            "duration_ms": 10.0 if case["id"] == "fast" else 100.0,
            "media_duration_seconds": 1.0,
            "file_size_bytes": 1,
            "chat_slot": 0,
        }

    monkeypatch.setattr("scripts.media_staging_matrix._run_case", fake_case)

    async def run():
        return await run_live(
            [
                {"id": "fast", "platform": "youtube", "url": "https://youtube.com/watch?v=x", "enabled": True},
                {"id": "slow", "platform": "tiktok", "url": "https://tiktok.com/@x/video/1", "enabled": True},
            ],
            download=True,
            concurrency=2,
        )

    import asyncio

    report = asyncio.run(run())
    assert report["latency_ms"]["p95"] == 100.0
    assert report["latency_ms"]["p50"] == 10.0
    assert report["requested_jobs"] == 2
    assert report["unique_chat_slots"] == 1
    assert report["max_active_jobs"] == 2
    assert report["latency_scope"] == "provider_probe_and_download_only"
    assert report["scope"] == "live-media-matrix"
    assert report["completion_slo"]["met"] is True


def test_media_matrix_expands_jobs_over_bounded_chat_slots(monkeypatch):
    async def fake_case(_provider, case, _semaphore, download, **kwargs):
        index = int(kwargs.get("run_index", 0))
        state = kwargs["execution_state"]
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        import asyncio
        await asyncio.sleep(0.001)
        state["active"] -= 1
        return {
            "case_id": case["id"],
            "run_index": index,
            "platform": case["platform"],
            "source_host": "example.test",
            "ok": True,
            "mode": "download" if download else "probe",
            "error_code": None,
            "duration_ms": float(index + 1),
            "media_duration_seconds": 1.0,
            "file_size_bytes": 1,
            "chat_slot": index % int(kwargs.get("chat_count", 1)),
        }

    monkeypatch.setattr("scripts.media_staging_matrix._run_case", fake_case)

    import asyncio

    async def run():
        return await run_live(
            [{"id": "youtube", "platform": "youtube", "url": "https://youtube.com/watch?v=x", "enabled": True}],
            download=False,
            concurrency=10,
            jobs=10,
            chat_count=100,
        )

    report = asyncio.run(run())
    assert report["case_count"] == 10
    assert report["requested_jobs"] == 10
    assert report["concurrency_limit"] == 10
    assert report["chat_count"] == 100
    assert report["unique_chat_slots"] == 10
    assert report["max_active_jobs"] == 10
    assert report["chat_scope_mode"] == "synthetic_slot_mapping_only"
    assert report["completion_slo"]["met"] is None
    assert report["platforms"]["youtube"] == {"jobs": 10, "passed": 10, "failed": 0}


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"concurrency": 11}, "live_concurrency_invalid"),
        ({"concurrency": 0}, "live_concurrency_invalid"),
        ({"concurrency": 1, "jobs": 101}, "live_jobs_invalid"),
        ({"concurrency": 1, "chat_count": 0}, "live_chat_count_invalid"),
    ],
)
def test_media_matrix_rejects_unsafe_external_load_bounds(monkeypatch, kwargs, error):
    async def run():
        return await run_live(
            [{"id": "youtube", "platform": "youtube", "url": "https://youtube.com/watch?v=x", "enabled": True}],
            download=False,
            **kwargs,
        )

    import asyncio

    with pytest.raises(MatrixError, match=error):
        asyncio.run(run())
