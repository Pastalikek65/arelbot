from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.staging_matrix import MatrixCase, MatrixConfigError, load_cases, run_case, run_matrix, write_report


def test_load_cases_rejects_duplicate_and_complex_parameters(tmp_path: Path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "same", "feature": "weather", "params": {}},
                    {"id": "same", "feature": "weather", "params": {}},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(MatrixConfigError):
        load_cases(duplicate, feature_keys={"weather"})

    complex_params = tmp_path / "complex.json"
    complex_params.write_text(
        json.dumps(
            {"cases": [{"id": "bad", "feature": "weather", "params": {"city": ["Ä°stanbul"]}}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(MatrixConfigError):
        load_cases(complex_params, feature_keys={"weather"})


@dataclass
class _Definition:
    cache_ttl_seconds: int = 60


@dataclass
class _Feature:
    definition: _Definition


class _Result:
    def __init__(
        self,
        *,
        ok=True,
        cache_hit=False,
        stale=False,
        source="Fixture",
        source_url="https://example.test/data",
    ):
        self.ok = ok
        self.cache_hit = cache_hit
        self.stale = stale
        self.source = source
        self.source_url = source_url
        self.error_code = None
        self.error_message = None


class _Executor:
    class _Registry:
        def get_feature(self, key):
            return _Feature(_Definition())

    registry = _Registry()

    def __init__(self):
        self.calls = 0

    async def execute(self, feature, params):
        self.calls += 1
        return _Result(cache_hit=self.calls > 1)


class _FailingExecutor(_Executor):
    async def execute(self, feature, params):
        raise RuntimeError("provider response contained a secret URL")


def test_run_case_requires_expected_source_and_cache_hit():
    executor = _Executor()
    case = MatrixCase("fixture", "weather", {"city": "Ä°stanbul"}, "example.test", True)
    result = asyncio.run(run_case(case, executor))
    assert result.ok is True
    assert result.second_cache_hit is True
    assert executor.calls == 2

    bad_source = MatrixCase("fixture", "weather", {}, "other.test", False)
    failed = asyncio.run(run_case(bad_source, _Executor()))
    assert failed.ok is False
    assert failed.detail == "source_host_mismatch"


def test_run_matrix_bounds_concurrency_and_reports_percentiles():
    class Engine:
        feature_executor = _Executor()

    cases = [MatrixCase(f"case_{i}", "weather", {}, "example.test", False) for i in range(3)]
    report = asyncio.run(run_matrix(cases, Engine(), concurrency=2))
    assert report["ok"] is True
    assert report["case_count"] == 3
    assert report["latency_ms"]["p95"] is not None
    with pytest.raises(MatrixConfigError):
        asyncio.run(run_matrix(cases, Engine(), concurrency=11))


def test_staging_failure_report_contains_only_stable_error_type():
    result = asyncio.run(
        run_matrix(
            [MatrixCase("failure", "weather", {}, "example.test", False)],
            type("Engine", (), {"feature_executor": _FailingExecutor()})(),
            concurrency=1,
        )
    )
    assert result["ok"] is False
    assert result["results"][0]["error_code"] == "STAGING_EXCEPTION"
    assert result["results"][0]["detail"] == "staging_case type=RuntimeError"


def test_staging_report_is_atomic_and_rejects_symlink_targets(tmp_path: Path):
    report = tmp_path / "reports" / "matrix.json"
    write_report(report, {"ok": True, "results": []})
    assert json.loads(report.read_text(encoding="utf-8"))["ok"] is True
    assert not list(report.parent.glob("*.tmp"))

    symlink = tmp_path / "symlink.json"
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    try:
        symlink.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(MatrixConfigError, match="symlink"):
        write_report(symlink, {"ok": False})
