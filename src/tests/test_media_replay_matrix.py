from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.media_replay_matrix import MatrixError, load_matrix, run_replay


ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_media_replay_matrix_covers_all_supported_platform_families():
    cases = load_matrix(ROOT / "docs" / "media-replay-matrix.json")

    assert {case["platform"] for case in cases} == {
        "instagram", "tiktok", "youtube", "x", "facebook", "reddit"
    }
    assert len(cases) == 6


def test_media_replay_matrix_rejects_platform_mismatch(tmp_path):
    config = tmp_path / "matrix.json"
    config.write_text(
        json.dumps({
            "cases": [{
                "id": "bad_case",
                "platform": "instagram",
                "url": "https://x.com/arel/status/replay-fixture",
            }]
        }),
        encoding="utf-8",
    )

    with pytest.raises(MatrixError, match="matrix_platform_mismatch"):
        load_matrix(config)


def test_media_replay_matrix_is_network_free_and_cleans_artifacts():
    cases = load_matrix(ROOT / "docs" / "media-replay-matrix.json")
    report = asyncio.run(run_replay(cases))

    assert report["ok"] is True
    assert report["network_calls"] == 0
    assert report["passed"] == 6
    assert report["failed"] == 0
    assert all(row["cleanup"] is True for row in report["results"])
    assert all(row["provider"] == f"replay-{row['platform']}" for row in report["results"])
    assert all("url" not in row and "path" not in row for row in report["results"])
