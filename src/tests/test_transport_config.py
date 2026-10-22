from __future__ import annotations

import importlib
import logging
from urllib.parse import unquote, urlparse

import pytest

from config import Config, build_mongodb_uri, validate_database_password


def _production_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "TELEGRAM_TOKEN", "fixture-token")
    monkeypatch.setattr(Config, "APP_ROLE", "worker")
    monkeypatch.setattr(Config, "APP_ENV", "development")
    monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://areladmin:fixture-secret@mongo/arelbot")
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


def test_production_configuration_requires_credentialed_mongodb_uri(monkeypatch):
    """A production deployment must not reach MongoDB anonymously."""

    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID", 123456789)
    monkeypatch.setattr(Config, "OWNER_ID_CONFIGURED", True, raising=False)
    monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://mongo:27017/arel_bot")

    with pytest.raises(ValueError, match="MONGODB_URI must include credentials"):
        Config.validate()


def test_build_mongodb_uri_encodes_the_password():
    uri = build_mongodb_uri("areladmin", "p@ss:word/1", "mongo:27017", "arel_bot")

    assert uri == "mongodb://areladmin:p%40ss%3Aword%2F1@mongo:27017/arel_bot"
    assert unquote(urlparse(uri).password or "") == "p@ss:word/1"


def test_build_mongodb_uri_names_the_authentication_source():
    """A user in ``admin`` cannot authenticate without the explicit source.

    The driver authenticates against the database in the path by default, so
    a connection string without ``authSource`` fails with
    ``Authentication failed.`` even when the password is correct.
    """

    uri = build_mongodb_uri("areladmin", "secret", "mongo:27017", "arel_bot", auth_source="admin")

    assert uri == "mongodb://areladmin:secret@mongo:27017/arel_bot?authSource=admin"
    assert "authSource=admin" in uri


def test_config_builds_an_authenticating_uri_from_the_application_credentials(monkeypatch):
    """The credential fallback must not produce an anonymous connection."""

    import config as config_module

    try:
        with monkeypatch.context() as patch:
            patch.setenv("MONGO_APP_USERNAME", "arel_app")
            patch.setenv("MONGO_APP_PASSWORD", "app-secret")
            patch.delenv("MONGODB_URI", raising=False)
            importlib.reload(config_module)
            # The application account lives in the application database, so the
            # connection string names that database as the authentication
            # source.
            assert config_module.Config.MONGODB_URI == (
                "mongodb://arel_app:app-secret@mongo:27017/arel_bot?authSource=arel_bot"
            )
    finally:
        importlib.reload(config_module)


def test_config_fallback_never_uses_the_administrative_account(monkeypatch):
    """The application must not run as the administrative account.

    A deployment that configures just the administrative credentials is a
    misconfiguration.  The fallback must leave the connection string empty
    instead of building it with the root account.
    """

    import config as config_module

    try:
        with monkeypatch.context() as patch:
            patch.setenv("MONGO_ROOT_USERNAME", "areladmin")
            patch.setenv("MONGO_ROOT_PASSWORD", "root-secret")
            patch.delenv("MONGO_APP_USERNAME", raising=False)
            patch.delenv("MONGO_APP_PASSWORD", raising=False)
            patch.delenv("MONGODB_URI", raising=False)
            importlib.reload(config_module)
            assert config_module.Config.MONGODB_URI is None
            assert config_module.Config.MONGO_ADMIN_ONLY_CREDENTIALS is True
    finally:
        importlib.reload(config_module)


def test_production_rejects_a_deployment_without_the_application_account(monkeypatch):
    """Production must not fall back to the administrative account."""

    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID", 123456789)
    monkeypatch.setattr(Config, "OWNER_ID_CONFIGURED", True, raising=False)
    monkeypatch.setattr(Config, "MONGO_APP_PASSWORD", "", raising=False)
    monkeypatch.setattr(Config, "MONGO_ROOT_PASSWORD", "", raising=False)
    monkeypatch.setattr(Config, "MONGO_ADMIN_ONLY_CREDENTIALS", True, raising=False)

    with pytest.raises(ValueError, match="MONGO_APP_USERNAME"):
        Config.validate()


def test_development_only_warns_when_the_administrative_account_is_the_only_one(monkeypatch, caplog):
    """Development keeps working, but the misconfiguration is reported."""

    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "MONGO_ADMIN_ONLY_CREDENTIALS", True, raising=False)

    with caplog.at_level(logging.WARNING, logger="config"):
        Config.validate()

    assert any("MONGO_APP_USERNAME" in record.getMessage() for record in caplog.records)


def test_database_password_charset_accepts_the_documented_characters():
    validate_database_password("arel-2024_app.secret~1", label="MONGO_APP_PASSWORD")


@pytest.mark.parametrize("value", ["", "p@ss", "p:ss", "p ss", "p/ss", "p#ss", "p%ss"])
def test_database_password_charset_rejects_reserved_characters(value):
    """A reserved character would silently break the connection string."""

    with pytest.raises(ValueError, match="MONGO_APP_PASSWORD"):
        validate_database_password(value, label="MONGO_APP_PASSWORD")


def test_config_module_imports_with_a_password_with_a_reserved_character(monkeypatch):
    """Importing the module must never fail because of the environment.

    A module that raises while it is imported takes down every tool that
    imports it, so the character set is enforced by ``Config.validate()``.
    """

    import config as config_module

    try:
        with monkeypatch.context() as patch:
            patch.setenv("MONGO_APP_USERNAME", "arel_app")
            patch.setenv("MONGO_APP_PASSWORD", "app@secret")
            patch.delenv("MONGODB_URI", raising=False)
            importlib.reload(config_module)
            # The fallback still builds a usable connection string.
            assert config_module.Config.MONGODB_URI == (
                "mongodb://arel_app:app%40secret@mongo:27017/arel_bot?authSource=arel_bot"
            )
    finally:
        importlib.reload(config_module)


def test_production_rejects_a_password_with_a_reserved_character(monkeypatch):
    """Production must not start with a password that breaks the URI."""

    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Config, "OWNER_ID", 123456789)
    monkeypatch.setattr(Config, "OWNER_ID_CONFIGURED", True, raising=False)
    monkeypatch.setattr(Config, "MONGO_APP_PASSWORD", "app@secret", raising=False)
    monkeypatch.setattr(Config, "MONGO_ROOT_PASSWORD", "", raising=False)

    with pytest.raises(ValueError, match="MONGO_APP_PASSWORD"):
        Config.validate()


def test_development_only_warns_about_a_password_with_a_reserved_character(monkeypatch, caplog):
    """Development keeps working and reports the character set."""

    _production_base(monkeypatch)
    monkeypatch.setattr(Config, "MONGO_APP_PASSWORD", "app@secret", raising=False)
    monkeypatch.setattr(Config, "MONGO_ROOT_PASSWORD", "", raising=False)

    with caplog.at_level(logging.WARNING, logger="config"):
        Config.validate()

    assert any("MONGO_APP_PASSWORD" in record.getMessage() for record in caplog.records)


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
