#!/usr/bin/env python3
"""Audit release evidence without promoting live checks implicitly.

The normal mode validates deterministic/fixture evidence and reports live
evidence as an explicitly separate section. ``--require-live`` is intended for
the deployment pipeline: it requires the configured live artifacts to be
present and successful instead of treating a local replay as production proof.
Only bounded check names and counts are emitted; evidence payloads are never
echoed to the terminal or copied into the audit report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = PROJECT_ROOT / "deploy" / "evidence"


@dataclass(frozen=True)
class AuditCheck:
    name: str
    ok: bool
    code: str | None = None


DETERMINISTIC_FILES = (
    "media-queue-load.json",
    "persistence-staging.json",
    "provider-staging.json",
    "media-staging-validation.json",
    "media-replay-matrix.json",
)
OBSERVED_LIVE_FILES = (
    "telegram-staging.json",
    "media-staging-concurrency-docker-rerun.json",
    "provider-governance-links.json",
    "vds-readiness.json",
)
DEPLOYMENT_REQUIRED_FILES = (
    "telegram-mutation-live.json",
    "media-staging-full-matrix.json",
    "vds-backup-restore.json",
    "monitoring-alert-delivery.json",
    "provider-governance-approval.json",
    "evren-staging.json",
)
TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
SENSITIVE_TEXT_RE = re.compile(r"(?i)\b(?:bearer\s+|api[_-]?key\s*[=:]|password\s*[=:]|cookie\s*[=:])")
_LIVE_MEDIA_PLATFORMS = frozenset({"instagram", "tiktok", "youtube", "x"})


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "root_not_object"
    return payload, None


def _contains_secret(value: Any, *, key: str = "") -> bool:
    """Reject obvious credential material while allowing boolean secret_free."""

    if isinstance(value, dict):
        for child_key, child_value in value.items():
            lowered = str(child_key).casefold()
            if lowered in {"secret_free", "last_error_present"}:
                if isinstance(child_value, bool):
                    continue
            if any(token in lowered for token in ("token", "cookie", "password", "authorization", "api_key", "private_key")):
                # Evidence may safely state that a value is absent, but must
                # never retain the value itself.
                if child_value not in (None, False, "", 0):
                    return True
            if _contains_secret(child_value, key=lowered):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret(item, key=key) for item in value)
    if isinstance(value, str):
        return bool(TOKEN_RE.search(value) or SENSITIVE_TEXT_RE.search(value))
    return False


def _bool(payload: dict[str, Any], key: str) -> bool:
    return payload.get(key) is True


def _audit_file(path: Path, *, required: bool) -> tuple[dict[str, Any] | None, list[AuditCheck]]:
    if not path.is_file():
        return None, [AuditCheck(path.name, not required, "missing" if required else "optional_missing")]
    payload, error = _read_json(path)
    if error:
        return None, [AuditCheck(path.name, False, error)]
    checks = [AuditCheck(f"{path.name}:json", True)]
    checks.append(AuditCheck(f"{path.name}:safe", not _contains_secret(payload), "secret_material" if _contains_secret(payload) else None))
    return payload, checks


def _all_true_mapping(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(item is True for item in value.values())


def _audit_live_invariants(filename: str, payload: dict[str, Any]) -> AuditCheck | None:
    """Validate the evidence contract required for a real deployment.

    Presence and ``ok=true`` alone are insufficient for live release evidence:
    a manually fabricated JSON file must not promote a deployment. The
    checks stay intentionally bounded and inspect only safe counters/flags.
    """

    if filename == "cleanapis-staging.json":
        ok = (
            payload.get("scope") == "cleanapis-staging"
            and _bool(payload, "ok")
            and payload.get("mode") == "live-completion"
            and payload.get("provider") == "cleanapis"
            and payload.get("model") == "deepseek-v4-flash-0731"
            and isinstance(payload.get("latency_ms"), (int, float))
            and 0 <= float(payload.get("latency_ms")) <= 120_000
            and isinstance(payload.get("response_chars"), int)
            and 0 <= payload.get("response_chars", -1) <= 256_000
            and isinstance(payload.get("usage_present"), bool)
            and payload.get("secret_free") is True
        )
        return AuditCheck("cleanapis_probe:invariants", ok, None if ok else "cleanapis_probe_invariant_failed")

    if filename == "evren-staging.json":
        ok = (
            payload.get("scope") == "evren-staging"
            and _bool(payload, "ok")
            and payload.get("mode") == "live-completion"
            and payload.get("provider") == "evren"
            and isinstance(payload.get("model"), str)
            and bool(payload.get("model"))
            and isinstance(payload.get("latency_ms"), (int, float))
            and 0 <= float(payload.get("latency_ms")) <= 120_000
            and isinstance(payload.get("response_chars"), int)
            and 0 <= payload.get("response_chars", -1) <= 256_000
            and isinstance(payload.get("usage_present"), bool)
            and payload.get("secret_free") is True
        )
        return AuditCheck("evren_probe:invariants", ok, None if ok else "evren_probe_invariant_failed")

    if filename == "vds-readiness.json":
        checks = payload.get("checks")
        required_checks = {
            "ssh_authenticated",
            "host_key_verified",
            "linux_host",
            "docker_available",
            "compose_available",
            "app_root_present",
            "app_root_writable",
            "shared_env_present",
        }
        runtime = payload.get("runtime_checks")
        runtime_ok = runtime is None or (
            isinstance(runtime, dict)
            and set(runtime) == {
                "compose_services_healthy",
                "bot_healthy",
                "worker_healthy",
                "mongo_healthy",
                "redis_healthy",
            }
            and all(type(value) is bool for value in runtime.values())
        )
        service_health = payload.get("service_health")
        service_health_ok = service_health is None or (
            isinstance(service_health, dict)
            and set(service_health) == {"bot", "worker", "mongo", "redis"}
            and all(type(value) is bool for value in service_health.values())
        )
        guardian = payload.get("guardian")
        guardian_ok = guardian is None or (
            isinstance(guardian, dict)
            and set(guardian) == {"mode", "socket_present", "socket_readable"}
            and guardian.get("mode") in {"disabled", "shadow", "active"}
            and type(guardian.get("socket_present")) is bool
            and type(guardian.get("socket_readable")) is bool
        )
        compose = payload.get("compose")
        compose_ok = compose is None or (
            isinstance(compose, dict)
            and set(compose) == {"file_present", "config_valid"}
            and type(compose.get("file_present")) is bool
            and type(compose.get("config_valid")) is bool
        )
        ok = (
            payload.get("scope") == "vds-readiness"
            and payload.get("mode") == "live-read-only"
            and payload.get("mutation") is False
            and isinstance(checks, dict)
            and set(checks) == required_checks
            and all(type(value) is bool for value in checks.values())
            and payload.get("secret_free") is True
            and isinstance(payload.get("release_count"), int)
            and 0 <= payload.get("release_count", -1) <= 10000
            and isinstance(payload.get("free_space_kb"), int)
            and 0 <= payload.get("free_space_kb", -1) <= 10**12
            and isinstance(payload.get("target_container_count"), int)
            and 0 <= payload.get("target_container_count", -1) <= 10000
            and isinstance(payload.get("existing_container_count"), int)
            and 0 <= payload.get("existing_container_count", -1) <= 10000
            and isinstance(payload.get("listening_ports"), list)
            and all(item in {80, 443, 8443} for item in payload.get("listening_ports", []))
            and isinstance(payload.get("blockers"), list)
            and all(isinstance(item, str) and re.fullmatch(r"[a-z0-9_]{1,80}", item) for item in payload.get("blockers", []))
            and runtime_ok
            and service_health_ok
            and guardian_ok
            and compose_ok
            and payload.get("ok") is (len(payload.get("blockers", [])) == 0)
            and (payload.get("ok") is False or payload.get("ok") is all(checks.values()))
        )
        return AuditCheck("vds_readiness:invariants", ok, None if ok else "vds_readiness_invariant_failed")

    if filename == "telegram-mutation-live.json":
        operations = payload.get("operations")
        ok = (
            payload.get("scope") == "telegram-mutation-live"
            and _bool(payload, "ok")
            and payload.get("failed") == 0
            and isinstance(payload.get("passed"), int)
            and payload.get("passed", 0) > 0
            and _all_true_mapping(operations)
            and payload.get("passed") == len(operations)
        )
        return AuditCheck("telegram_mutation:invariants", ok, None if ok else "telegram_mutation_invariant_failed")

    if filename == "media-staging-full-matrix.json":
        platforms = payload.get("platforms")
        slo = payload.get("completion_slo")
        latency = payload.get("latency_ms")
        waived_raw = payload.get("waived_platforms", [])
        waived = {str(item).casefold() for item in waived_raw} if isinstance(waived_raw, list) else set()
        waiver = payload.get("waiver")
        waiver_ok = not waived
        if waived:
            waiver_ok = (
                waived <= _LIVE_MEDIA_PLATFORMS
                and isinstance(waiver, dict)
                and isinstance(waiver.get("approved_by"), str)
                and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,_:/()'_-]{2,240}", waiver["approved_by"]))
                and isinstance(waiver.get("reason"), str)
                and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,_:/()'_-]{2,240}", waiver["reason"]))
            )
        required_platforms = _LIVE_MEDIA_PLATFORMS - waived
        ok = (
            payload.get("scope") == "live-media-matrix"
            and _bool(payload, "ok")
            and payload.get("mode") == "live-download"
            and isinstance(payload.get("case_count"), int)
            and payload.get("case_count", 0) >= len(required_platforms)
            and payload.get("passed") == payload.get("case_count")
            and payload.get("failed") == 0
            and isinstance(platforms, dict)
            and set(platforms) >= required_platforms
            and all(
                isinstance(platforms[name], dict)
                and platforms[name].get("jobs", 0) >= 1
                and platforms[name].get("failed") == 0
                for name in required_platforms
            )
            and waiver_ok
            and isinstance(slo, dict)
            and slo.get("met") is True
            and isinstance(slo.get("p95_ms"), (int, float))
            and float(slo.get("p95_ms")) <= 10_000
            and isinstance(latency, dict)
            and isinstance(latency.get("p95"), (int, float))
            and float(latency.get("p95")) <= 10_000
            and isinstance(payload.get("chat_count"), int)
            and payload.get("chat_count", 0) >= 100
            and isinstance(payload.get("concurrency_limit"), int)
            and 1 <= payload.get("concurrency_limit", 0) <= 10
            and isinstance(payload.get("max_active_jobs"), int)
            and int(payload.get("max_active_jobs", 0)) <= payload.get("concurrency_limit", 0)
        )
        return AuditCheck("live_media_matrix:invariants", ok, None if ok else "live_media_matrix_invariant_failed")

    if filename == "vds-backup-restore.json":
        checks = payload.get("checks")
        ok = (
            payload.get("scope") == "vds-backup-restore"
            and _bool(payload, "ok")
            and _all_true_mapping(checks)
            and isinstance(payload.get("archive_sha256"), str)
            and bool(re.fullmatch(r"[0-9a-fA-F]{64}", payload["archive_sha256"]))
        )
        return AuditCheck("vds_backup_restore:invariants", ok, None if ok else "vds_backup_restore_invariant_failed")

    if filename == "monitoring-alert-delivery.json":
        alerts = payload.get("alerts")
        ok = (
            payload.get("scope") == "live-monitoring-alert-delivery"
            and _bool(payload, "ok")
            and _all_true_mapping(alerts)
            and isinstance(payload.get("delivered"), int)
            and payload.get("delivered", 0) == len(alerts)
            and payload.get("failed") == 0
        )
        return AuditCheck("monitoring_delivery:invariants", ok, None if ok else "monitoring_delivery_invariant_failed")

    if filename == "provider-governance-approval.json":
        features = payload.get("features")
        ok = (
            payload.get("scope") == "provider-governance-approval"
            and _bool(payload, "ok")
            and isinstance(features, list)
            and bool(features)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("feature_key"), str)
                and bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item["feature_key"]))
                and item.get("status") == "approved"
                and isinstance(item.get("evidence_id"), str)
                and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", item["evidence_id"]))
                for item in features
            )
            and payload.get("approved_count") == len(features)
            and payload.get("pending_count") == 0
            and payload.get("rejected_count") == 0
        )
        return AuditCheck("provider_approval:invariants", ok, None if ok else "provider_approval_invariant_failed")

    return None


def _audit_deterministic(evidence_dir: Path) -> tuple[list[AuditCheck], dict[str, Any]]:
    checks: list[AuditCheck] = []
    payloads: dict[str, Any] = {}
    for filename in DETERMINISTIC_FILES:
        payload, rows = _audit_file(evidence_dir / filename, required=True)
        checks.extend(rows)
        if payload is not None:
            payloads[filename] = payload

    queue = payloads.get("media-queue-load.json")
    if queue is not None:
        ok = (
            _bool(queue, "ok")
            and queue.get("completed") == queue.get("jobs")
            and queue.get("failed") == 0
            and queue.get("queue_depth") == 0
            and int(queue.get("max_active_workers", -1)) <= int(queue.get("workers", -2))
            and int(queue.get("active_chats", 0)) >= 100
        )
        checks.append(AuditCheck("media_queue:invariants", ok, None if ok else "queue_invariant_failed"))

    persistence = payloads.get("persistence-staging.json")
    if persistence is not None:
        rows = persistence.get("checks")
        ok = _bool(persistence, "ok") and isinstance(rows, dict) and all(value is True for value in rows.values())
        checks.append(AuditCheck("persistence:invariants", ok, None if ok else "persistence_invariant_failed"))

    provider = payloads.get("provider-staging.json")
    if provider is not None:
        ok = (
            _bool(provider, "ok")
            and provider.get("passed") == provider.get("case_count")
            and provider.get("failed") == 0
            and isinstance(provider.get("repeat_latency_ms"), dict)
        )
        checks.append(AuditCheck("provider_staging:invariants", ok, None if ok else "provider_invariant_failed"))

    media_validation = payloads.get("media-staging-validation.json")
    if media_validation is not None:
        ok = _bool(media_validation, "ok") and media_validation.get("disabled_count", 0) >= 0
        checks.append(AuditCheck("media_validation:invariants", ok, None if ok else "media_validation_failed"))

    media_replay = payloads.get("media-replay-matrix.json")
    if media_replay is not None:
        platforms = media_replay.get("platforms")
        ok = (
            _bool(media_replay, "ok")
            and media_replay.get("passed") == media_replay.get("case_count")
            and media_replay.get("failed") == 0
            and media_replay.get("network_calls") == 0
            and isinstance(platforms, dict)
            and {str(item) for item in platforms} >= {
                "instagram", "tiktok", "youtube", "x", "facebook", "reddit"
            }
        )
        checks.append(AuditCheck("media_replay:invariants", ok, None if ok else "media_replay_invariant_failed"))
    return checks, payloads


def _audit_live(evidence_dir: Path, *, require_live: bool) -> tuple[list[AuditCheck], dict[str, Any]]:
    checks: list[AuditCheck] = []
    payloads: dict[str, Any] = {}
    for filename in OBSERVED_LIVE_FILES:
        payload, rows = _audit_file(evidence_dir / filename, required=require_live)
        checks.extend(rows)
        if payload is not None:
            payloads[filename] = payload
            if filename == "provider-governance-links.json":
                links = payload.get("links") if isinstance(payload.get("links"), dict) else payload
                results = links.get("results") if isinstance(links, dict) else None
                ok = (
                    isinstance(links, dict)
                    and links.get("scope") == "live-link-health"
                    and links.get("approval_unchanged") is True
                    and isinstance(results, list)
                    and links.get("link_count") == len(results)
                    and links.get("passed", -1) + links.get("failed", -1) == len(results)
                )
                checks.append(
                    AuditCheck(
                        "provider_governance_links:invariants",
                        ok,
                        None if ok else "provider_link_invariant_failed",
                    )
                )
            invariant = _audit_live_invariants(filename, payload)
            if invariant is not None:
                checks.append(invariant)
            if require_live:
                ok = _bool(payload, "ok")
                checks.append(AuditCheck(f"{filename}:success", ok, None if ok else "live_evidence_failed"))
    for filename in DEPLOYMENT_REQUIRED_FILES:
        payload, rows = _audit_file(evidence_dir / filename, required=require_live)
        checks.extend(rows)
        if payload is not None:
            payloads[filename] = payload
            invariant = _audit_live_invariants(filename, payload)
            if invariant is not None:
                checks.append(invariant)
            if require_live:
                ok = _bool(payload, "ok")
                checks.append(AuditCheck(f"{filename}:success", ok, None if ok else "deployment_evidence_failed"))
    return checks, payloads


def audit(evidence_dir: Path, *, require_live: bool = False) -> dict[str, Any]:
    deterministic, deterministic_payloads = _audit_deterministic(evidence_dir)
    live, live_payloads = _audit_live(evidence_dir, require_live=require_live)
    checks = [*deterministic, *live]
    report = {
        "ok": all(item.ok for item in checks),
        "scope": "deterministic_plus_live" if require_live else "deterministic",
        "checks": [asdict(item) for item in checks],
        "deterministic_files": len(deterministic_payloads),
        "live_files": len(live_payloads),
        "live_required": require_live,
    }
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(args.evidence_dir, require_live=args.require_live)
    if args.report:
        _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
