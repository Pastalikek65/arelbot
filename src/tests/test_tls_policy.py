from __future__ import annotations

import ssl

import pytest

from src.security.tls import httpx_verify_kwargs, tls_verify


def test_tls_policy_uses_explicit_existing_bundle(tmp_path, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    bundle = tmp_path / "ca.pem"
    bundle.write_text("fixture", encoding="utf-8")

    assert tls_verify(cert_file=str(bundle), is_windows=False) == str(bundle)
    assert httpx_verify_kwargs()["verify"] is not None


def test_tls_policy_rejects_missing_explicit_bundle(tmp_path):
    with pytest.raises(ValueError, match="SSL_CERT_FILE"):
        tls_verify(cert_file=str(tmp_path / "missing.pem"), is_windows=False)


def test_tls_policy_keeps_native_windows_verification_enabled(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    value = tls_verify(cert_file="", is_windows=True)
    assert isinstance(value, ssl.SSLContext)
    assert value.verify_mode == ssl.CERT_REQUIRED
    assert value.check_hostname is True


def test_tls_policy_uses_default_httpx_verification_on_non_windows(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert tls_verify(cert_file="", is_windows=False) is True


def test_telegram_request_always_sets_tls_verification(monkeypatch):
    import src.telegram_request as telegram_request

    monkeypatch.setattr(telegram_request, "tls_verify", lambda: True)
    kwargs = telegram_request._request_kwargs()
    assert kwargs["httpx_kwargs"] == {"verify": True}


def test_architecture_guard_rejects_tls_bypass(monkeypatch, tmp_path):
    import scripts.architecture_check as architecture_check

    (tmp_path / "unsafe.py").write_text("verify=False\n", encoding="utf-8")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)
    errors = architecture_check.check_tls_policy()
    assert errors and "TLS verification bypass" in errors[0]
