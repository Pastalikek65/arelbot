from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.production_preflight import (
    CheckResult,
    PreflightError,
    preflight,
    read_dotenv,
    validate_production_env,
)
from src.webhook_proxy_contract import WebhookProxyContract


def _valid_values() -> dict[str, str]:
    return {
        "TELEGRAM_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyz",
        "OWNER_ID": "123456789",
        "APP_ENV": "production",
        "TELEGRAM_MODE": "webhook",
        "WEBHOOK_URL": "https://bot.example.test/telegram/webhook",
        "WEBHOOK_SECRET": "A" * 32,
        "MEDIA_COOKIE_ENABLED": "false",
        "MONGODB_REQUIRED": "true",
        "REDIS_REQUIRED": "true",
        "MEDIA_REDIS_REQUIRED": "true",
        "MONGODB_URI": "mongodb://mongo:27017/arel_bot",
        "REDIS_URL": "redis://redis:6379/0",
    }


def test_production_env_contract_accepts_safe_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in _valid_values().items()) + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    values = read_dotenv(env_file)
    checks = validate_production_env(values, env_file=env_file)
    assert all(check.ok for check in checks)


def test_production_preflight_rejects_staging_runtime_profile():
    values = _valid_values()
    values["STAGING_PROFILE_PATH"] = "/etc/arelbot/staging-core-profile.json"

    checks = validate_production_env(values)

    assert any(
        check.code == "staging_profile_not_allowed_in_production"
        for check in checks
        if not check.ok
    )


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("OWNER_ID", "your_telegram_user_id_here", "owner_id_missing_or_invalid"),
        ("APP_ENV", "development", "app_env_not_production"),
        ("TELEGRAM_MODE", "long-polling", "telegram_mode_invalid"),
        ("WEBHOOK_URL", "http://bot.example.test", "webhook_url_invalid"),
        ("WEBHOOK_URL", "https://user:pass@bot.example.test", "webhook_url_invalid"),
        ("WEBHOOK_SECRET", "short", "webhook_secret_invalid"),
    ],
)
def test_production_env_contract_rejects_unsafe_values(key: str, value: str, code: str):
    values = _valid_values()
    values[key] = value
    checks = validate_production_env(values)
    assert code in {check.code for check in checks if not check.ok}


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("WEBHOOK_PATH", "../unsafe", "webhook_path_invalid"),
        ("WEBHOOK_PATH", "telegram//webhook", "webhook_path_invalid"),
        ("WEBHOOK_PORT", "70000", "webhook_port_invalid"),
        ("WEBHOOK_LISTEN", "", "webhook_listen_invalid"),
    ],
)
def test_production_preflight_reuses_telegram_transport_contract(key: str, value: str, code: str):
    values = _valid_values()
    values[key] = value
    checks = validate_production_env(values)
    assert code in {check.code for check in checks if not check.ok}


def test_cookie_mode_requires_non_placeholder_encryption_key():
    values = _valid_values()
    values["MEDIA_COOKIE_ENABLED"] = "true"
    values["ENCRYPTION_KEY"] = "replace-with-a-random-secret-at-least-32-characters"
    checks = validate_production_env(values)
    assert any(check.code == "encryption_key_invalid" for check in checks if not check.ok)


def test_production_env_contract_accepts_explicit_polling_without_webhook():
    values = _valid_values()
    values.update(
        {
            "TELEGRAM_MODE": "polling",
            "WEBHOOK_URL": "",
            "WEBHOOK_SECRET": "",
        }
    )

    checks = validate_production_env(values)

    assert all(check.ok for check in checks)
    assert {check.name for check in checks if check.code == "not_required_for_polling"} == {
        "webhook_url",
        "webhook_secret",
    }


def test_production_preflight_rejects_memory_fallbacks():
    values = _valid_values()
    values["MONGODB_REQUIRED"] = "false"
    values["REDIS_REQUIRED"] = "false"
    values["MEDIA_REDIS_REQUIRED"] = "false"
    checks = validate_production_env(values)
    codes = {check.code for check in checks if not check.ok}
    assert {"mongodb_required_disabled", "redis_required_disabled"} <= codes


def test_production_preflight_rejects_legacy_provider_fallback():
    values = _valid_values()
    values["LEGACY_PROVIDER_FALLBACK_ENABLED"] = "true"
    checks = validate_production_env(values)
    assert any(check.code == "legacy_provider_fallback_enabled" for check in checks if not check.ok)


def test_production_preflight_rejects_disabled_provider_approval_guard():
    values = _valid_values()
    values["REQUIRE_PROVIDER_APPROVAL"] = "false"
    checks = validate_production_env(values)
    assert any(check.code == "provider_approval_required_disabled" for check in checks if not check.ok)


def test_production_preflight_can_require_explicit_provider_signoff(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in _valid_values().items()) + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    with pytest.raises(PreflightError, match="provider_governance_not_approved"):
        preflight(
            env_file=env_file,
            compose_file=tmp_path / "docker-compose.yml",
            check_compose=False,
            require_provider_approval=True,
        )


def test_production_preflight_passes_disabled_features_to_governance_gate(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    values = _valid_values()
    values["DISABLED_FEATURES"] = "weather,breaking_news"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    captured = {}

    def fake_governance(**kwargs):
        captured.update(kwargs)
        return CheckResult("provider_governance", True)

    monkeypatch.setattr("scripts.production_preflight.run_provider_governance", fake_governance)
    preflight(
        env_file=env_file,
        compose_file=tmp_path / "docker-compose.yml",
        check_compose=False,
        require_provider_approval=True,
    )

    assert captured["disabled_features"] == {"weather", "breaking_news"}


def test_preflight_rejects_missing_provider_approval_manifest(tmp_path: Path):
    env_file = tmp_path / ".env"
    values = _valid_values()
    values["PROVIDER_APPROVAL_MANIFEST"] = "provider-approvals.json"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    with pytest.raises(PreflightError, match="provider_approval_manifest_missing"):
        preflight(env_file=env_file, compose_file=tmp_path / "docker-compose.yml", check_compose=False)


def test_production_preflight_rejects_missing_database_endpoints():
    values = _valid_values()
    values["MONGODB_URI"] = ""
    values["REDIS_URL"] = "http://redis.example.test"
    checks = validate_production_env(values)
    codes = {check.code for check in checks if not check.ok}
    assert {"mongodb_uri_invalid", "redis_uri_invalid"} <= codes


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("FEATURE_FLAGS", "not-json", "feature_flags_invalid"),
        ("FEATURE_FLAGS", '{"weather": "false"}', "feature_flags_invalid"),
        ("DISABLED_FEATURES", "weather,unsafe flag", "disabled_features_invalid"),
    ],
)
def test_production_preflight_validates_feature_kill_switches(key: str, value: str, code: str):
    values = _valid_values()
    values[key] = value
    checks = validate_production_env(values)
    assert code in {check.code for check in checks if not check.ok}


def test_production_preflight_accepts_typed_feature_kill_switches():
    values = _valid_values()
    values["FEATURE_FLAGS"] = '{"weather": false, "public.weather": true}'
    values["DISABLED_FEATURES"] = "breaking_news,public.weather"
    checks = validate_production_env(values)
    assert all(check.ok for check in checks)


def test_public_observability_requires_a_valid_host_secret_file(tmp_path: Path):
    values = _valid_values()
    values.update(
        {
            "OBSERVABILITY_ENABLED": "true",
            "OBSERVABILITY_LISTEN": "0.0.0.0",
            "ARELBOT_OBSERVABILITY_TOKEN_FILE": str(tmp_path / "missing.token"),
        }
    )
    checks = validate_production_env(values)
    assert any(check.code == "observability_token_file_missing" for check in checks if not check.ok)

    token_file = tmp_path / "metrics.token"
    token_file.write_text("metrics-file-token-123456\n", encoding="utf-8")
    token_file.chmod(0o600)
    values["ARELBOT_OBSERVABILITY_TOKEN_FILE"] = str(token_file)
    checks = validate_production_env(values)
    assert all(check.ok for check in checks)


def test_monitoring_requires_observability_and_grafana_password_file(tmp_path: Path):
    values = _valid_values()
    values["MONITORING_ENABLED"] = "true"
    values["OBSERVABILITY_ENABLED"] = "true"
    values["OBSERVABILITY_LISTEN"] = "0.0.0.0"
    values["ARELBOT_OBSERVABILITY_TOKEN_FILE"] = str(tmp_path / "metrics.token")
    values["GRAFANA_ADMIN_PASSWORD_FILE"] = str(tmp_path / "grafana.password")
    values["ARELBOT_ALERT_WEBHOOK_URL_FILE"] = str(tmp_path / "alert-webhook.url")
    (tmp_path / "metrics.token").write_text("metrics-file-token-123456\n", encoding="utf-8")
    (tmp_path / "grafana.password").write_text("a-long-grafana-secret", encoding="utf-8")
    (tmp_path / "alert-webhook.url").write_text("https://alerts.example.com/arelbot", encoding="utf-8")
    (tmp_path / "metrics.token").chmod(0o600)
    (tmp_path / "grafana.password").chmod(0o600)
    (tmp_path / "alert-webhook.url").chmod(0o600)

    checks = validate_production_env(values)
    assert all(check.ok for check in checks)

    values["OBSERVABILITY_ENABLED"] = "false"
    checks = validate_production_env(values)
    assert any(check.code == "monitoring_observability_disabled" for check in checks if not check.ok)

    values["OBSERVABILITY_ENABLED"] = "true"
    (tmp_path / "grafana.password").unlink()
    checks = validate_production_env(values)
    assert any(check.code == "grafana_admin_password_file_missing" for check in checks if not check.ok)


@pytest.mark.parametrize(
    "target",
    [
        "http://alerts.example.com/arelbot",
        "https://user:password@alerts.example.com/arelbot",
        "https://127.0.0.1/arelbot",
        "https://localhost/arelbot",
        "https://alerts.example.com/arelbot?token=secret",
    ],
)
def test_monitoring_rejects_unsafe_alert_webhook_url_files(tmp_path: Path, target: str):
    values = _valid_values()
    values.update(
        {
            "MONITORING_ENABLED": "true",
            "OBSERVABILITY_ENABLED": "true",
            "OBSERVABILITY_LISTEN": "127.0.0.1",
            "GRAFANA_ADMIN_PASSWORD_FILE": str(tmp_path / "grafana.password"),
            "ARELBOT_ALERT_WEBHOOK_URL_FILE": str(tmp_path / "alert-webhook.url"),
        }
    )
    (tmp_path / "grafana.password").write_text("a-long-grafana-secret", encoding="utf-8")
    (tmp_path / "alert-webhook.url").write_text(target, encoding="utf-8")
    (tmp_path / "grafana.password").chmod(0o600)
    (tmp_path / "alert-webhook.url").chmod(0o600)

    failures = [check for check in validate_production_env(values) if not check.ok]
    assert any(check.name == "alert_webhook_url_file" for check in failures)
    assert all(target not in str(check) for check in failures)


def test_preflight_resolves_relative_observability_secret_from_env_directory(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("fixture=true\n", encoding="utf-8")
    env_file.chmod(0o600)
    token_file = tmp_path / "metrics.token"
    token_file.write_text("metrics-file-token-123456\n", encoding="utf-8")
    token_file.chmod(0o600)
    values = _valid_values()
    values.update(
        {
            "OBSERVABILITY_ENABLED": "true",
            "OBSERVABILITY_LISTEN": "0.0.0.0",
            "ARELBOT_OBSERVABILITY_TOKEN_FILE": "metrics.token",
        }
    )
    checks = validate_production_env(values, env_file=env_file)
    assert all(check.ok for check in checks)


def test_preflight_surfaces_missing_env_without_echoing_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TOKEN=super-secret\n", encoding="utf-8")
    with pytest.raises(PreflightError) as exc_info:
        preflight(env_file=env_file, compose_file=tmp_path / "docker-compose.yml", check_compose=False)
    assert "super-secret" not in str(exc_info.value)
    assert "telegram_token_invalid" in str(exc_info.value)


def test_preflight_script_runs_from_a_different_working_directory(tmp_path: Path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "production_preflight.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--nginx-config" in result.stdout


def test_preflight_validates_reverse_proxy_contract_when_configured(tmp_path: Path):
    env_file = tmp_path / ".env"
    values = _valid_values()
    values["WEBHOOK_URL"] = "https://bot.example.test/bot"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    env_file.chmod(0o600)

    contract = WebhookProxyContract.from_values(values["WEBHOOK_URL"], "telegram/webhook")
    nginx_file = tmp_path / "arelbot.conf"
    nginx_file.write_text(
        "\n".join(
            [
                "upstream arelbot_bot {",
                "    server 127.0.0.1:8443;",
                "}",
                "server {",
                "    listen 443 ssl http2;",
                f"    location = {contract.public_path} {{",
                f"        proxy_pass http://arelbot_bot{contract.upstream_path};",
                "        proxy_set_header X-Telegram-Bot-Api-Secret-Token $http_x_telegram_bot_api_secret_token;",
                "    }",
                "    location / { return 404; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    checks = preflight(
        env_file=env_file,
        compose_file=tmp_path / "docker-compose.yml",
        check_compose=False,
        nginx_config=nginx_file,
    )
    assert any(check.name == "webhook_proxy_contract" for check in checks)

    nginx_file.write_text(nginx_file.read_text(encoding="utf-8").replace(contract.public_path, "/wrong"), encoding="utf-8")
    with pytest.raises(PreflightError, match="webhook_location_mismatch"):
        preflight(
            env_file=env_file,
            compose_file=tmp_path / "docker-compose.yml",
            check_compose=False,
            nginx_config=nginx_file,
        )
