#!/usr/bin/env python3
"""Run a bounded, read-only readiness check against the Linux VDS.

The probe never creates containers, changes files, mutates Nginx, or reads the
remote environment.  A live run requires ``--live``, ``--confirm-live`` and
``ARELBOT_VDS_READINESS_LIVE=1``.  SSH host keys are loaded from the operator's
known-hosts file and unknown keys are rejected.  Reports intentionally contain
only boolean/counter facts; credentials, hostnames, container names, paths and
remote diagnostics are omitted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    import paramiko
except ImportError:  # pragma: no cover - deployment-only dependency
    paramiko = None


class VdsReadinessError(ValueError):
    """Stable, secret-free readiness failure."""


_PORT_RE = re.compile(r"(?:^|:)\s*(?P<port>[0-9]{1,5})\s+\S")
_COUNT_RE = re.compile(r"^[0-9]+$")
_ALLOWED_PORTS = frozenset({80, 443, 8443})
_MIN_FREE_SPACE_KB = 512 * 1024
_EXPECTED_SERVICES = frozenset({"bot", "worker", "mongo", "redis"})
_GUARDIAN_MODES = frozenset({"disabled", "shadow", "active"})


def validate_live_gate(*, live: bool, confirm_live: bool, env_value: str | None) -> None:
    if not (live and confirm_live and str(env_value or "").strip() == "1"):
        raise VdsReadinessError("vds_readiness_live_gate_required")


def parse_release_count(output: str) -> int | None:
    """Parse the exact ``find | wc -l`` result without trusting shell text."""

    value = str(output or "").strip()
    if not _COUNT_RE.fullmatch(value):
        return None
    count = int(value)
    return count if 0 <= count <= 10000 else None


def parse_free_space_kb(output: str) -> int | None:
    """Parse the exact available-kilobyte value emitted by ``df -Pk``."""

    value = str(output or "").strip()
    if not _COUNT_RE.fullmatch(value):
        return None
    available = int(value)
    return available if 0 <= available <= 10**12 else None


def parse_port_list(output: str) -> list[int]:
    """Extract only known listening ports from ``ss`` output."""

    ports: set[int] = set()
    for line in str(output or "").splitlines():
        for match in re.finditer(r":(?P<port>[0-9]{1,5})(?:\s|$)", line):
            value = int(match.group("port"))
            if value in _ALLOWED_PORTS:
                ports.add(value)
    return sorted(ports)


def _bool_marker(output: str, expected: str) -> bool:
    return str(output or "").strip() == expected


def parse_service_health(output: str) -> dict[str, bool]:
    """Parse bounded Docker service status output without retaining names/details.

    The remote probe emits ``service|status`` rows.  Only the four production
    services are accepted and a service is healthy when Docker reports the
    healthcheck as ``(healthy)``.  Unknown rows are ignored so an unrelated
    container cannot accidentally satisfy a readiness check.
    """

    result = {service: False for service in _EXPECTED_SERVICES}
    for raw_line in str(output or "").splitlines()[:64]:
        service, separator, status = raw_line.partition("|")
        if not separator or service not in _EXPECTED_SERVICES:
            continue
        normalized = status.strip().casefold()
        result[service] = "(healthy)" in normalized or normalized == "healthy"
    return result


def parse_guardian_mode(output: str) -> str:
    """Normalize the non-secret Guardian mode read from the shared env."""

    mode = str(output or "").strip().casefold()
    return mode if mode in _GUARDIAN_MODES else "disabled"


def build_report(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build the stable, secret-free readiness artifact from parsed facts."""

    checks = {
        "ssh_authenticated": bool(snapshot.get("ssh_authenticated")),
        "host_key_verified": bool(snapshot.get("host_key_verified")),
        "linux_host": bool(snapshot.get("linux_host")),
        "docker_available": bool(snapshot.get("docker_available")),
        "compose_available": bool(snapshot.get("compose_available")),
        "app_root_present": bool(snapshot.get("app_root_present")),
        "app_root_writable": bool(snapshot.get("app_root_writable")),
        "shared_env_present": bool(snapshot.get("shared_env_present")),
    }
    blockers: list[str] = []
    if not checks["ssh_authenticated"]:
        blockers.append("ssh_unavailable")
    if not checks["host_key_verified"]:
        blockers.append("host_key_unverified")
    if not checks["linux_host"]:
        blockers.append("linux_host_required")
    if not checks["docker_available"]:
        blockers.append("docker_missing")
    if not checks["compose_available"]:
        blockers.append("compose_missing")
    if not checks["app_root_present"]:
        blockers.append("app_root_missing")
    if not checks["app_root_writable"]:
        blockers.append("app_root_not_writable")
    if not checks["shared_env_present"]:
        blockers.append("shared_env_missing")
    free_space_kb = int(snapshot.get("free_space_kb") or 0)
    if free_space_kb < _MIN_FREE_SPACE_KB:
        blockers.append("disk_space_low")

    if "compose_file_present" in snapshot and not bool(snapshot.get("compose_file_present")):
        blockers.append("compose_file_missing")
    if "compose_config_valid" in snapshot and not bool(snapshot.get("compose_config_valid")):
        blockers.append("compose_config_invalid")

    service_health_raw = snapshot.get("service_health")
    service_health: dict[str, bool] | None = None
    if isinstance(service_health_raw, Mapping):
        service_health = {
            service: bool(service_health_raw.get(service))
            for service in sorted(_EXPECTED_SERVICES)
        }
        for service, healthy in service_health.items():
            if not healthy:
                blockers.append(f"{service}_unhealthy")

    guardian_mode = parse_guardian_mode(snapshot.get("guardian_mode", "disabled"))
    guardian_socket_present = bool(snapshot.get("guardian_socket_present"))
    guardian_socket_readable = bool(snapshot.get("guardian_socket_readable"))
    guardian = {
        "mode": guardian_mode,
        "socket_present": guardian_socket_present,
        "socket_readable": guardian_socket_readable,
    }
    if guardian_mode in {"shadow", "active"} and not guardian_socket_present:
        blockers.append("guardian_socket_missing")
    if guardian_mode in {"shadow", "active"} and guardian_socket_present and not guardian_socket_readable:
        blockers.append("guardian_socket_unreadable")

    report = {
        "scope": "vds-readiness",
        "ok": not blockers,
        "mode": "live-read-only",
        "mutation": False,
        "checks": checks,
        "release_count": max(0, int(snapshot.get("release_count") or 0)),
        "free_space_kb": free_space_kb,
        "target_container_count": max(0, int(snapshot.get("target_container_count") or 0)),
        "nginx_active": bool(snapshot.get("nginx_active")),
        "listening_ports": sorted({int(port) for port in snapshot.get("listening_ports", []) if int(port) in _ALLOWED_PORTS}),
        "existing_container_count": max(0, int(snapshot.get("existing_container_count") or 0)),
        "blockers": blockers,
        "secret_free": True,
    }
    # These details are included only for a real remote probe.  Keeping them
    # optional preserves compatibility with older local fixture snapshots.
    if service_health is not None:
        report["service_health"] = service_health
        report["runtime_checks"] = {
            "compose_services_healthy": all(service_health.values()),
            "bot_healthy": service_health["bot"],
            "worker_healthy": service_health["worker"],
            "mongo_healthy": service_health["mongo"],
            "redis_healthy": service_health["redis"],
        }
    if "compose_file_present" in snapshot or "compose_config_valid" in snapshot:
        report["compose"] = {
            "file_present": bool(snapshot.get("compose_file_present")),
            "config_valid": bool(snapshot.get("compose_config_valid")),
        }
    report["guardian"] = guardian
    return report


def _remote_snapshot(client: Any, *, app_root: str) -> dict[str, Any]:
    """Run only fixed, read-only commands and return parsed facts."""

    root = str(app_root).strip()
    if not root or any(char in root for char in "\n\r;|&`$'") or not root.startswith("/"):
        raise VdsReadinessError("app_root_invalid")
    commands = {
        "linux": "uname -s",
        "docker": "command -v docker >/dev/null 2>&1 && echo yes || echo no",
        "compose": "docker compose version >/dev/null 2>&1 && echo yes || echo no",
        "app_root": f"test -d {root} && echo yes || echo no",
        "app_root_writable": f"test -w {root} && echo yes || echo no",
        "shared_env": f"test -f {root}/shared/.env && echo yes || echo no",
        "releases": f"find {root}/releases -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | wc -l",
        "disk": f"df -Pk {root} 2>/dev/null | awk 'NR==2 {{print $4}}'",
        "nginx": "systemctl is-active nginx 2>/dev/null || true",
        "ports": "ss -ltnH 2>/dev/null || true",
        "containers": "docker ps -q 2>/dev/null | wc -l",
        "target_containers": "docker ps -q --filter label=com.docker.compose.project=arelbot-prod 2>/dev/null | wc -l",
        "compose_file": f"test -f {root}/current/docker-compose.yml && echo yes || echo no",
        "compose_config": f"cd {root}/current 2>/dev/null && docker compose --profile prod config --quiet >/dev/null 2>&1 && echo yes || echo no",
        "services": "docker ps --filter label=com.docker.compose.project=arelbot-prod --format '{{.Label \"com.docker.compose.service\"}}|{{.Status}}' 2>/dev/null",
        "guardian_mode": f"awk -F= '$1==\"GUARDIAN_MODE\" {{print $2; exit}}' {root}/shared/.env 2>/dev/null",
        "guardian_socket": "test -S /run/kek-guardian/guardian.sock && echo yes || echo no",
        "guardian_socket_readable": "test -S /run/kek-guardian/guardian.sock && test -r /run/kek-guardian/guardian.sock && echo yes || echo no",
    }
    outputs: dict[str, str] = {}
    for key, command in commands.items():
        try:
            _, stdout, _ = client.exec_command(command, timeout=20)
            outputs[key] = stdout.read().decode("utf-8", "replace")[:4096]
        except Exception as exc:
            raise VdsReadinessError("remote_probe_failed") from exc
    release_count = parse_release_count(outputs["releases"])
    container_count = parse_release_count(outputs["containers"])
    target_container_count = parse_release_count(outputs["target_containers"])
    free_space_kb = parse_free_space_kb(outputs["disk"])
    if release_count is None or container_count is None or target_container_count is None or free_space_kb is None:
        raise VdsReadinessError("remote_counter_invalid")
    service_health = parse_service_health(outputs["services"])
    return {
        "ssh_authenticated": True,
        "host_key_verified": True,
        "linux_host": _bool_marker(outputs["linux"], "Linux"),
        "docker_available": _bool_marker(outputs["docker"], "yes"),
        "compose_available": _bool_marker(outputs["compose"], "yes"),
        "app_root_present": _bool_marker(outputs["app_root"], "yes"),
        "app_root_writable": _bool_marker(outputs["app_root_writable"], "yes"),
        "shared_env_present": _bool_marker(outputs["shared_env"], "yes"),
        "release_count": release_count,
        "free_space_kb": free_space_kb,
        "target_container_count": target_container_count,
        "nginx_active": _bool_marker(outputs["nginx"], "active"),
        "listening_ports": parse_port_list(outputs["ports"]),
        "existing_container_count": container_count,
        "compose_file_present": _bool_marker(outputs["compose_file"], "yes"),
        "compose_config_valid": _bool_marker(outputs["compose_config"], "yes"),
        "service_health": service_health,
        "guardian_mode": parse_guardian_mode(outputs["guardian_mode"]),
        "guardian_socket_present": _bool_marker(outputs["guardian_socket"], "yes"),
        "guardian_socket_readable": _bool_marker(outputs["guardian_socket_readable"], "yes"),
    }


def run_live(*, host: str, user: str, port: int, known_hosts: Path, password: str, app_root: str) -> dict[str, Any]:
    if paramiko is None:
        raise VdsReadinessError("paramiko_missing")
    if not known_hosts.is_file() or known_hosts.is_symlink():
        raise VdsReadinessError("known_hosts_missing")
    if not str(password or ""):
        raise VdsReadinessError("ssh_credential_missing")
    client = paramiko.SSHClient()
    try:
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(hostname=str(host), port=int(port), username=str(user), password=password, timeout=20)
        except paramiko.AuthenticationException as exc:
            raise VdsReadinessError("ssh_authentication_failed") from exc
        snapshot = _remote_snapshot(client, app_root=app_root)
        return build_report(snapshot)
    except VdsReadinessError:
        raise
    except (paramiko.SSHException, OSError) as exc:
        raise VdsReadinessError("ssh_connection_failed") from exc
    finally:
        client.close()


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise VdsReadinessError("report_target_symlink")
    for parent in (path.parent, *path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise VdsReadinessError("report_parent_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    try:
        temporary.write_text(json.dumps(dict(payload), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh" / "known_hosts"))
    parser.add_argument("--password-env", default="AREL_SSH_PASSWORD")
    parser.add_argument("--app-root", default="/opt/arel")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if not (args.live or args.confirm_live):
            payload = {
                "scope": "vds-readiness-validation",
                "ok": True,
                "mode": "validation-only",
                "checks": ["live_gate_not_entered", "read_only_contract"],
                "secret_free": True,
            }
        else:
            validate_live_gate(live=args.live, confirm_live=args.confirm_live, env_value=os.getenv("ARELBOT_VDS_READINESS_LIVE"))
            payload = run_live(
                host=args.host,
                user=args.user,
                port=args.port,
                known_hosts=Path(args.known_hosts).expanduser().resolve(),
                password=os.getenv(str(args.password_env), ""),
                app_root=args.app_root,
            )
    except VdsReadinessError as exc:
        payload = {"scope": "vds-readiness", "ok": False, "error": str(exc), "secret_free": True}
    except Exception:
        payload = {"scope": "vds-readiness", "ok": False, "error": "vds_readiness_failed", "secret_free": True}
    if args.report:
        try:
            _write_report(args.report, payload)
        except VdsReadinessError:
            payload = {"scope": "vds-readiness", "ok": False, "error": "report_write_failed", "secret_free": True}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"vds_readiness={'ok' if payload.get('ok') else 'failed'}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
