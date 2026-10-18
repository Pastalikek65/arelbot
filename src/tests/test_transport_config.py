from __future__ import annotations

import pytest

from config import Config


def _production_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "TELEGRAM_TOKEN", "fixture-token")
    monkeypatch.setattr(Config, "APP_ROLE", "worker")
    monkeypatch.setattr(Config, "APP_ENV", "development")
    monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://mongo/arelbot")
    monkeypatch.setattr(Config, "TELEGRAM_MODE", "polling")
    monkeypatch.setattr(Config, "WEBHOOK_URL", None)
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", None)
    monkeypatch.setattr(Config, "WEBHOOK_PATH", "telegram/webhook")
    monkeypatch.setattr(Config, "WEBHOOK_PORT", 8443)
    monkeypatch.setattr(Config, "STAGING_PROFILE_PATH", "", raising=False)
    monkeypatch.setattr(Config, "OWNER_ID_CONFIGURED", False, raising=False)


def test_polling_configuration_does_not_require_webhook_values(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    Config.validate()


def test_invalid_tls_bundle_fails_configuration_validation(monkeypatch, tmp_path):
    _production_base(monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    with pytest.raises(ValueError, match="SSL_CERT_FILE"):
        Config.validate()


def test_production_requires_explicit_owner_id(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID", 5544654560)
    with pytest.raises(ValueError, match="OWNER_ID must be explicitly configured"):
        Config.validate()


def test_production_accepts_owner_id_that_matches_legacy_sentinel_when_env_explicit(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID", 5544654560)
    monkeypatch.setattr(Config, "OWNER_ID_CONFIGURED", True, raising=False)
    Config.validate()


def test_production_accepts_configured_owner_id(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID", 123456789)
    Config.validate()


def test_staging_profile_cannot_be_attached_to_production(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID_CONFIGURED", True, raising=False)
    monkeypatch.setattr(Config, "STAGING_PROFILE_PATH", "/etc/arelbot/staging-core-profile.json")

    with pytest.raises(ValueError, match="STAGING_PROFILE_PATH requires APP_ENV=staging"):
        Config.validate()


def test_worker_with_provider_embeddings_requires_an_ai_key(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "RAG_USE_PROVIDER_EMBEDDINGS", True)
    monkeypatch.setattr(Config, "FREEMODEL_KEY", None)
    monkeypatch.setattr(Config, "GROQ_KEYS", [])
    monkeypatch.setattr(Config, "GEMINI_KEYS", [])
    monkeypatch.setattr(Config, "CEREBRAS_KEYS", [])
    monkeypatch.setattr(Config, "IAMHC_KEY", None)
    monkeypatch.setattr(Config, "CLEANAPIS_KEY", None)
    monkeypatch.setattr(Config, "NINEROUTER_KEY", None)
    with pytest.raises(ValueError, match="RAG embeddings"):
        Config.validate()


def test_webhook_configuration_requires_credential_free_https_and_strong_secret(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "TELEGRAM_MODE", "webhook")
    monkeypatch.setattr(Config, "WEBHOOK_URL", "https://bot.example.test")
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "A" * 32)
    Config.validate()


@pytest.mark.parametrize(
    "url, secret, expected",
    [
        ("http://bot.example.test", "A" * 32, "HTTPS"),
        ("https://user:pass@bot.example.test", "A" * 32, "credential-free"),
        ("https://bot.example.test", "short", "16-256"),
    ],
)
def test_webhook_configuration_rejects_unsafe_transport_values(monkeypatch, url, secret, expected):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "TELEGRAM_MODE", "webhook")
    monkeypatch.setattr(Config, "WEBHOOK_URL", url)
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", secret)

    with pytest.raises(ValueError, match=expected):
        Config.validate()


def test_webhook_configuration_rejects_invalid_port_or_path(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "TELEGRAM_MODE", "webhook")
    monkeypatch.setattr(Config, "WEBHOOK_URL", "https://bot.example.test")
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "A" * 32)
    monkeypatch.setattr(Config, "WEBHOOK_PORT", 70000)
    with pytest.raises(ValueError, match="WEBHOOK_PORT"):
        Config.validate()

    monkeypatch.setattr(Config, "WEBHOOK_PORT", 8443)
    monkeypatch.setattr(Config, "WEBHOOK_PATH", "../unsafe")
    with pytest.raises(ValueError, match="WEBHOOK_PATH"):
        Config.validate()


def test_observability_requires_a_token_when_bound_publicly(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "OBSERVABILITY_ENABLED", True)
    monkeypatch.setattr(Config, "OBSERVABILITY_LISTEN", "0.0.0.0")
    monkeypatch.setattr(Config, "OBSERVABILITY_TOKEN", None)
    with pytest.raises(ValueError, match="OBSERVABILITY_TOKEN"):
        Config.validate()


def test_loopback_observability_can_run_without_token(monkeypatch):
    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "OBSERVABILITY_ENABLED", True)
    monkeypatch.setattr(Config, "OBSERVABILITY_LISTEN", "127.0.0.1")
    monkeypatch.setattr(Config, "OBSERVABILITY_TOKEN", None)
    Config.validate()


def test_observability_token_file_is_loaded_without_echoing_value(monkeypatch, tmp_path):
    _production_base(monkeypatch)
    token_path = tmp_path / "observability.token"
    token_path.write_text("metrics-file-token-123456\n", encoding="utf-8")
    monkeypatch.setattr(Config, "OBSERVABILITY_ENABLED", True)
    monkeypatch.setattr(Config, "OBSERVABILITY_LISTEN", "0.0.0.0")
    monkeypatch.setattr(Config, "OBSERVABILITY_TOKEN", None)
    monkeypatch.setattr(Config, "OBSERVABILITY_TOKEN_FILE", str(token_path))
    Config.load_observability_token()
    assert Config.OBSERVABILITY_TOKEN == "metrics-file-token-123456"
    Config.validate()
