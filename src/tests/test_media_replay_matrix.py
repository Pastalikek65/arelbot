from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.media_replay_matrix import MatrixError, _write_report, load_matrix, run_replay


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


def test_media_replay_matrix_rejects_credentials_and_out_of_scope_ports(tmp_path):
    for url, error in (
        ("https://user:password@x.com/arel/status/replay-fixture", "matrix_fixture_url_unsafe"),
        ("https://x.com:8443/arel/status/replay-fixture", "matrix_fixture_port_invalid"),
    ):
        config = tmp_path / f"{error}.json"
        config.write_text(
            json.dumps({"cases": [{"id": "x_case", "platform": "x", "url": url}]}),
            encoding="utf-8",
        )
        with pytest.raises(MatrixError, match=error):
            load_matrix(config)


def test_media_replay_report_rejects_secret_material_and_symlink_parents(tmp_path):
    report = tmp_path / "replay.json"
    with pytest.raises(MatrixError, match="report_secret_material"):
        _write_report(report, {"scope": "replay", "authorization": "Bearer secret"})
    link = tmp_path / "linked"
    link.mkdir()
    symlink = tmp_path / "alias"
    try:
        symlink.symlink_to(link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(MatrixError, match="report_parent_symlink"):
        _write_report(symlink / "replay.json", {"secret_free": True})


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
