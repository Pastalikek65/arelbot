#!/usr/bin/env python3
"""Validate the non-secret production contract before a VDS deploy.

The preflight intentionally reports only check names and safe error codes. It
never echoes dotenv values, compose output, connection URIs or Telegram
credentials. The same validator is used by the deploy script and by tests so
production startup failures are deterministic rather than discovered halfway
through a rollout.
"""

from __future__ import annotations

import argparse
import json
import ipaddress
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.webhook_proxy_contract import WebhookProxyContract, validate_nginx_webhook_config
from src.telegram_transport import TelegramTransport


class PreflightError(ValueError):
    """Raised when a production deployment contract is not satisfied."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    code: str | None = None


_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
_OWNER_RE = re.compile(r"^[1-9]\d{0,19}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_FEATURE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_PLACEHOLDERS = frozenset(
    {
        "",
        "your_telegram_bot_token_here",
        "your_telegram_user_id_here",
        "replace-with-a-random-secret-at-least-32-characters",
        "changeme",
        "change-me",
    }
)


def read_dotenv(path: Path) -> dict[str, str]:
    """Read the small dotenv subset used by deployment without interpolation."""

    if not path.is_file():
        raise PreflightError("env_file_missing")
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PreflightError("env_file_unreadable") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise PreflightError("env_line_invalid")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise PreflightError("env_key_invalid")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _non_placeholder(values: dict[str, str], key: str) -> bool:
    value = values.get(key, "").strip()
    return bool(value) and value.casefold() not in _PLACEHOLDERS


def _enabled(values: dict[str, str], key: str) -> bool:
    return values.get(key, "false").strip().casefold() in {"1", "true", "yes", "on"}


def _check_secret_file(
    values: dict[str, str],
    key: str,
    *,
    result_name: str,
    base_dir: Path | None = None,
) -> CheckResult:
    """Validate a host-mounted secret without returning its path or contents."""

    raw_path = values.get(key, "").strip()
    if not raw_path:
        return CheckResult(result_name, False, f"{result_name}_missing")
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        return CheckResult(result_name, False, f"{result_name}_missing")
    if os.name != "nt":
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            return CheckResult(result_name, False, f"{result_name}_unreadable")
        if mode & 0o077:
            return CheckResult(result_name, False, f"{result_name}_permissions_too_open")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return CheckResult(result_name, False, f"{result_name}_unreadable")
    valid = bool(_SECRET_RE.fullmatch(value))
    return CheckResult(result_name, valid, None if valid else f"{result_name}_invalid")


def _check_password_file(
    values: dict[str, str],
    key: str,
    *,
    result_name: str,
    base_dir: Path | None = None,
) -> CheckResult:
    """Validate a mounted UI password without imposing URL-safe token rules."""

    raw_path = values.get(key, "").strip()
    if not raw_path:
        return CheckResult(result_name, False, f"{result_name}_missing")
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        return CheckResult(result_name, False, f"{result_name}_missing")
    if os.name != "nt":
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            return CheckResult(result_name, False, f"{result_name}_unreadable")
        if mode & 0o077:
            return CheckResult(result_name, False, f"{result_name}_permissions_too_open")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return CheckResult(result_name, False, f"{result_name}_unreadable")
    valid = 12 <= len(value) <= 256 and "\x00" not in value and value.casefold() not in _PLACEHOLDERS
    return CheckResult(result_name, valid, None if valid else f"{result_name}_invalid")


def _check_alert_webhook_file(
    values: dict[str, str],
    key: str,
    *,
    result_name: str,
    base_dir: Path | None = None,
) -> CheckResult:
    """Validate the external Alertmanager receiver URL without echoing it."""

    raw_path = values.get(key, "").strip()
    if not raw_path:
        return CheckResult(result_name, False, f"{result_name}_missing")
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        return CheckResult(result_name, False, f"{result_name}_missing")
    if os.name != "nt":
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            return CheckResult(result_name, False, f"{result_name}_unreadable")
        if mode & 0o077:
            return CheckResult(result_name, False, f"{result_name}_permissions_too_open")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return CheckResult(result_name, False, f"{result_name}_unreadable")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return CheckResult(result_name, False, f"{result_name}_invalid")
    private_host = host in {"localhost", "localhost.localdomain"} or host.endswith(".local")
    try:
        address = ipaddress.ip_address(host)
        private_host = private_host or (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        )
    except ValueError:
        pass
    valid = bool(value) and len(value) <= 2048 and parsed.scheme == "https" and bool(host) and not private_host
    valid = valid and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    valid = valid and (port is None or 1 <= port <= 65535)
    return CheckResult(result_name, valid, None if valid else f"{result_name}_invalid")


def _check_file_permissions(path: Path) -> CheckResult:
    if os.name == "nt":
        return CheckResult("env_permissions", True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return CheckResult("env_permissions", False, "env_file_unreadable")
    return CheckResult("env_permissions", (mode & 0o077) == 0, "env_permissions_too_open")


def _valid_feature_flags(raw: str) -> bool:
    if not raw.strip():
        return True
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and all(
        bool(_FEATURE_NAME_RE.fullmatch(str(name))) and isinstance(value, bool)
        for name, value in payload.items()
    )


def _valid_disabled_features(raw: str) -> bool:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return all(bool(_FEATURE_NAME_RE.fullmatch(item)) for item in values)


def validate_production_env(values: dict[str, str], *, env_file: Path | None = None) -> list[CheckResult]:
    """Return safe, named checks for the production Telegram contract."""

    checks: list[CheckResult] = []

    token_ok = bool(_TOKEN_RE.fullmatch(values.get("TELEGRAM_TOKEN", "").strip()))
    checks.append(CheckResult("telegram_token", token_ok, None if token_ok else "telegram_token_invalid"))

    owner_ok = bool(_OWNER_RE.fullmatch(values.get("OWNER_ID", "").strip()))
    checks.append(CheckResult("owner_id", owner_ok, None if owner_ok else "owner_id_missing_or_invalid"))

    app_env_ok = values.get("APP_ENV", "").strip().casefold() == "production"
    checks.append(CheckResult("app_env", app_env_ok, None if app_env_ok else "app_env_not_production"))

    staging_profile_configured = bool(values.get("STAGING_PROFILE_PATH", "").strip())
    checks.append(
        CheckResult(
            "staging_profile",
            not staging_profile_configured,
            None if not staging_profile_configured else "staging_profile_not_allowed_in_production",
        )
    )

    provider_approval_ok = values.get("REQUIRE_PROVIDER_APPROVAL", "true").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    checks.append(
        CheckResult(
            "provider_approval_required",
            provider_approval_ok,
            None if provider_approval_ok else "provider_approval_required_disabled",
        )
    )

    mode = values.get("TELEGRAM_MODE", "").strip().casefold()
    mode_ok = mode in {"webhook", "polling"}
    checks.append(CheckResult("telegram_mode", mode_ok, None if mode_ok else "telegram_mode_invalid"))

    # Webhook deployments require an HTTPS reverse proxy contract. Polling is
    # an explicit production mode for hosts without a public domain; it does
    # not need a URL, certificate, secret or Nginx location.
    if mode == "webhook":
        try:
            webhook = urlsplit(values.get("WEBHOOK_URL", "").strip())
            webhook_ok = bool(
                webhook.scheme == "https"
                and webhook.hostname
                and not webhook.username
                and not webhook.password
                and not webhook.query
                and not webhook.fragment
            )
        except ValueError:
            webhook_ok = False
        checks.append(CheckResult("webhook_url", webhook_ok, None if webhook_ok else "webhook_url_invalid"))

        if webhook_ok:
            try:
                WebhookProxyContract.from_values(
                    values.get("WEBHOOK_URL", ""),
                    values.get("WEBHOOK_PATH", "telegram/webhook"),
                )
            except ValueError as exc:
                code = str(exc) if str(exc) in {"webhook_path_invalid", "webhook_url_path_invalid"} else "webhook_path_invalid"
                checks.append(CheckResult("webhook_path", False, code))
            else:
                checks.append(CheckResult("webhook_path", True))

        secret_ok = bool(_SECRET_RE.fullmatch(values.get("WEBHOOK_SECRET", "").strip()))
        checks.append(CheckResult("webhook_secret", secret_ok, None if secret_ok else "webhook_secret_invalid"))
    elif mode == "polling":
        checks.extend(
            [
                CheckResult("webhook_url", True, "not_required_for_polling"),
                CheckResult("webhook_secret", True, "not_required_for_polling"),
            ]
        )

    # Keep deployment validation on the exact same transport contract used by
    # ``main.py``. The individual checks above provide stable operator-facing
    # error codes; this shared construction catches drift before startup.
    try:
        transport = TelegramTransport(
            mode=mode,
            webhook_url=values.get("WEBHOOK_URL") if mode == "webhook" else None,
            webhook_listen=values.get("WEBHOOK_LISTEN", "0.0.0.0"),
            webhook_port=int(values.get("WEBHOOK_PORT", "8443")),
            webhook_path=values.get("WEBHOOK_PATH", "telegram/webhook"),
            webhook_secret=values.get("WEBHOOK_SECRET") if mode == "webhook" else None,
        )
        if mode == "webhook":
            transport.webhook_kwargs()
    except (TypeError, ValueError) as exc:
        message = str(exc)
        if "WEBHOOK_SECRET" in message:
            code = "webhook_secret_invalid"
        elif "WEBHOOK_PORT" in message:
            code = "webhook_port_invalid"
        elif "WEBHOOK_LISTEN" in message:
            code = "webhook_listen_invalid"
        elif "WEBHOOK_PATH" in message:
            code = "webhook_path_invalid"
        elif "WEBHOOK_URL path" in message:
            code = "webhook_url_path_invalid"
        else:
            code = "webhook_transport_invalid"
        checks.append(CheckResult("telegram_transport", False, code))
    else:
        checks.append(CheckResult("telegram_transport", True))

    cookie_enabled = values.get("MEDIA_COOKIE_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    encryption_ok = not cookie_enabled or (
        _non_placeholder(values, "ENCRYPTION_KEY") and len(values.get("ENCRYPTION_KEY", "")) >= 32
    )
    checks.append(CheckResult("encryption_key", encryption_ok, None if encryption_ok else "encryption_key_invalid"))

    mongo_required = _enabled(values, "MONGODB_REQUIRED")
    checks.append(CheckResult("mongodb_required", mongo_required, None if mongo_required else "mongodb_required_disabled"))
    try:
        mongo_uri = urlsplit(values.get("MONGODB_URI", "").strip())
        mongo_uri_ok = mongo_uri.scheme in {"mongodb", "mongodb+srv"} and bool(mongo_uri.hostname)
    except ValueError:
        mongo_uri_ok = False
    checks.append(CheckResult("mongodb_uri", mongo_uri_ok, None if mongo_uri_ok else "mongodb_uri_invalid"))
    redis_required = _enabled(values, "REDIS_REQUIRED") or _enabled(values, "MEDIA_REDIS_REQUIRED")
    checks.append(CheckResult("redis_required", redis_required, None if redis_required else "redis_required_disabled"))
    try:
        redis_uri = urlsplit(values.get("REDIS_URL", "").strip())
        redis_uri_ok = redis_uri.scheme in {"redis", "rediss"} and bool(redis_uri.hostname)
    except ValueError:
        redis_uri_ok = False
    checks.append(CheckResult("redis_uri", redis_uri_ok, None if redis_uri_ok else "redis_uri_invalid"))

    feature_flags_ok = _valid_feature_flags(values.get("FEATURE_FLAGS", ""))
    checks.append(CheckResult("feature_flags", feature_flags_ok, None if feature_flags_ok else "feature_flags_invalid"))
    disabled_features_ok = _valid_disabled_features(values.get("DISABLED_FEATURES", ""))
    checks.append(
        CheckResult(
            "disabled_features",
            disabled_features_ok,
            None if disabled_features_ok else "disabled_features_invalid",
        )
    )

    # Legacy adapters are retained for controlled migration/replay only. A
    # production deployment must fail closed instead of silently bypassing
    # typed provider contracts and their source governance metadata.
    legacy_fallback_enabled = _enabled(values, "LEGACY_PROVIDER_FALLBACK_ENABLED")
    checks.append(
        CheckResult(
            "legacy_provider_fallback",
            not legacy_fallback_enabled,
            None if not legacy_fallback_enabled else "legacy_provider_fallback_enabled",
        )
    )

    if _enabled(values, "OBSERVABILITY_ENABLED"):
        listen = values.get("OBSERVABILITY_LISTEN", "127.0.0.1").strip().casefold()
        local_bind = listen in {"localhost", "127.0.0.1", "::1", "[::1]"}
        inline_token = values.get("OBSERVABILITY_TOKEN", "").strip()
        inline_ok = bool(_SECRET_RE.fullmatch(inline_token))
        if inline_token and not inline_ok:
            checks.append(CheckResult("observability_token", False, "observability_token_invalid"))
        if not local_bind:
            if inline_ok:
                checks.append(CheckResult("observability_token", True))
            else:
                checks.append(
                    _check_secret_file(
                        values,
                        "ARELBOT_OBSERVABILITY_TOKEN_FILE",
                        result_name="observability_token_file",
                        base_dir=env_file.parent if env_file is not None else None,
                    )
                )

    if _enabled(values, "MONITORING_ENABLED"):
        if not _enabled(values, "OBSERVABILITY_ENABLED"):
            checks.append(CheckResult("monitoring_observability", False, "monitoring_observability_disabled"))
        else:
            checks.append(CheckResult("monitoring_observability", True))
        checks.append(
            _check_password_file(
                values,
                "GRAFANA_ADMIN_PASSWORD_FILE",
                result_name="grafana_admin_password_file",
                base_dir=env_file.parent if env_file is not None else None,
            )
        )
        checks.append(
            _check_alert_webhook_file(
                values,
                "ARELBOT_ALERT_WEBHOOK_URL_FILE",
                result_name="alert_webhook_url_file",
                base_dir=env_file.parent if env_file is not None else None,
            )
        )

    if env_file is not None:
        checks.append(_check_file_permissions(env_file))
    return checks


def run_compose_config(*, env_file: Path, compose_file: Path) -> CheckResult:
    """Ask Docker Compose to resolve the production profile without starting it."""

    if not compose_file.is_file():
        return CheckResult("compose_config", False, "compose_file_missing")
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
        "--profile",
        "prod",
        "config",
        "--quiet",
    ]
    try:
        result = subprocess.run(command, cwd=str(compose_file.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        return CheckResult("compose_config", False, "docker_missing")
    return CheckResult("compose_config", result.returncode == 0, None if result.returncode == 0 else "compose_config_invalid")


def run_webhook_proxy_contract(*, values: dict[str, str], nginx_config: Path) -> CheckResult:
    """Validate a host Nginx config without exposing its path or contents."""

    if not nginx_config.is_file():
        return CheckResult("webhook_proxy_contract", False, "webhook_proxy_config_missing")
    try:
        text = nginx_config.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return CheckResult("webhook_proxy_contract", False, "webhook_proxy_config_unreadable")
    try:
        contract = WebhookProxyContract.from_values(
            values.get("WEBHOOK_URL", ""),
            values.get("WEBHOOK_PATH", "telegram/webhook"),
        )
    except ValueError:
        return CheckResult("webhook_proxy_contract", False, "webhook_proxy_contract_invalid")
    errors = validate_nginx_webhook_config(text, contract)
    return CheckResult("webhook_proxy_contract", not errors, errors[0] if errors else None)


def run_provider_governance(
    *,
    require_approved: bool = True,
    approval_manifest: Path | None = None,
    disabled_features: set[str] | frozenset[str] | None = None,
) -> CheckResult:
    """Run the offline source-governance gate without echoing its report."""

    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "provider_governance.py")]
    if require_approved:
        command.append("--require-approved")
    if approval_manifest is not None:
        command.extend(["--approval-manifest", str(approval_manifest)])
    disabled = sorted({str(item).strip().casefold() for item in (disabled_features or set()) if str(item).strip()})
    if disabled:
        command.extend(["--disabled-features", ",".join(disabled)])
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CheckResult("provider_governance", False, "provider_governance_unavailable")
    return CheckResult(
        "provider_governance",
        result.returncode == 0,
        None if result.returncode == 0 else "provider_governance_not_approved",
    )


def preflight(
    *,
    env_file: Path,
    compose_file: Path,
    check_compose: bool = True,
    nginx_config: Path | None = None,
    require_provider_approval: bool = False,
    provider_approval_manifest: Path | None = None,
) -> list[CheckResult]:
    values = read_dotenv(env_file)
    checks = validate_production_env(values, env_file=env_file)
    manifest_value = str(values.get("PROVIDER_APPROVAL_MANIFEST", "")).strip()
    manifest_path = provider_approval_manifest
    if manifest_path is None and manifest_value:
        manifest_path = Path(manifest_value)
        if not manifest_path.is_absolute():
            manifest_path = env_file.parent / manifest_path
    if manifest_path is not None:
        manifest_path = manifest_path.resolve()
        checks.append(
            CheckResult(
                "provider_approval_manifest",
                manifest_path.is_file() and not manifest_path.is_symlink(),
                None if manifest_path.is_file() and not manifest_path.is_symlink() else "provider_approval_manifest_missing",
            )
        )
    if nginx_config is not None and values.get("TELEGRAM_MODE", "").strip().casefold() == "webhook":
        checks.append(run_webhook_proxy_contract(values=values, nginx_config=nginx_config))
    if require_provider_approval:
        disabled_features = {
            item.strip().casefold()
            for item in values.get("DISABLED_FEATURES", "").split(",")
            if item.strip()
        }
        checks.append(
            run_provider_governance(
                approval_manifest=manifest_path,
                disabled_features=disabled_features,
            )
        )
    if check_compose:
        checks.append(run_compose_config(env_file=env_file, compose_file=compose_file))
    failed = [item for item in checks if not item.ok]
    if failed:
        raise PreflightError(",".join(item.code or item.name for item in failed))
    return checks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument(
        "--nginx-config",
        type=Path,
        help="validate the deployed Nginx webhook location and secret forwarding",
    )
    parser.add_argument("--skip-compose", action="store_true", help="validate dotenv only")
    parser.add_argument(
        "--require-provider-approval",
        action="store_true",
        help="fail unless every external provider has recorded production sign-off",
    )
    parser.add_argument(
        "--provider-approval-manifest",
        type=Path,
        help="operator-reviewed fingerprint-bound provider approval evidence",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        checks = preflight(
            env_file=args.env_file.resolve(),
            compose_file=args.compose_file.resolve(),
            check_compose=not args.skip_compose,
            nginx_config=args.nginx_config.resolve() if args.nginx_config else None,
            require_provider_approval=args.require_provider_approval,
            provider_approval_manifest=args.provider_approval_manifest,
        )
    except PreflightError as exc:
        payload = {"ok": False, "error_codes": [code for code in str(exc).split(",") if code]}
        print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"production_preflight=failed codes={','.join(payload['error_codes'])}")
        return 1
    payload = {"ok": True, "checks": [item.name for item in checks]}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"production_preflight=ok checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
