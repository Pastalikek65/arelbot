"""Authorize Arel Bot to use a ChatGPT plan through OpenAI's SIWC flow.

Tokens are written outside the repository to an ACL-protected per-user path.
The script never prints the callback URL, authorization code, tokens, or
upstream response bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import csv
import hashlib
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.chatgpt_plan import (
    MODELS_URL,
    OAUTH_TOKEN_URL,
    PLAN_SCOPE,
    RESOURCE_SCOPE,
    ChatGPTPlanCredentials,
    _atomic_write_record,
    _b64url_decode,
    _load_record,
)

ISSUER = "https://auth.openai.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZATION_URL = f"{ISSUER}/api/accounts/authorize"
REQUIRED_SCOPES = {"openid", "profile", "email", "offline_access", RESOURCE_SCOPE, PLAN_SCOPE}


def _default_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ArelBot" / "chatgpt-plan"
    return Path.home() / ".arelbot" / "chatgpt-plan"


def _restrict_windows_acl(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        return
    whoami = shutil.which("whoami")
    icacls = shutil.which("icacls")
    if not whoami or not icacls:
        raise RuntimeError("windows_acl_tools_missing")
    result = subprocess.run(
        [whoami, "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    try:
        _, sid = next(csv.reader([result.stdout.strip()]))
    except Exception as exc:
        raise RuntimeError("windows_user_sid_unavailable") from exc
    if result.returncode or not sid.startswith("S-"):
        raise RuntimeError("windows_user_sid_unavailable")
    inheritance = "(OI)(CI)F" if directory else "F"
    args = [
        icacls,
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:{inheritance}",
        "/grant:r",
        "*S-1-5-18:(OI)(CI)F" if directory else "*S-1-5-18:F",
    ]
    acl = subprocess.run(args, check=False, capture_output=True, timeout=10)
    if acl.returncode:
        raise RuntimeError("windows_acl_update_failed")


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _restrict_windows_acl(path, directory=True)


def _host_id(directory: Path) -> str:
    host_path = directory / "vm-host-id.txt"
    if host_path.is_symlink():
        raise RuntimeError("host_id_path_unsafe")
    if host_path.exists():
        value = host_path.read_text(encoding="utf-8").strip()
        if value.startswith("urn:uuid:") and len(value) <= 64:
            return value
        raise RuntimeError("host_id_invalid")
    value = f"urn:uuid:{__import__('uuid').uuid4()}"
    temp = host_path.with_name(f".{host_path.name}.{os.getpid()}.tmp")
    temp.write_text(value + "\n", encoding="utf-8")
    os.replace(temp, host_path)
    _restrict_windows_acl(host_path, directory=False)
    return value


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_jwt_part(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(_b64url_decode(raw).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("id_token_invalid") from exc
    if not isinstance(result, dict):
        raise TypeError("id_token_invalid")
    return result


def _verify_id_token(id_token: str, *, client_id: str, nonce: str, metadata: dict[str, Any], jwks: dict[str, Any]) -> dict[str, Any]:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise RuntimeError("id_token_invalid")
    header = _decode_jwt_part(parts[0])
    claims = _decode_jwt_part(parts[1])
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise RuntimeError("id_token_algorithm_unsupported")
    keys = jwks.get("keys")
    jwk = next((item for item in keys or [] if isinstance(item, dict) and item.get("kid") == header["kid"]), None)
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
        raise RuntimeError("id_token_key_unavailable")
    try:
        public_numbers = rsa.RSAPublicNumbers(
            int.from_bytes(_b64url_decode(jwk["e"]), "big"),
            int.from_bytes(_b64url_decode(jwk["n"]), "big"),
        )
        public_key = public_numbers.public_key()
        signature = _b64url_decode(parts[2])
        public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("id_token_signature_invalid") from exc
    issuer = metadata.get("issuer")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    now = time.time()
    if issuer != ISSUER or claims.get("iss") != ISSUER:
        raise RuntimeError("id_token_issuer_invalid")
    if client_id not in audiences or not isinstance(claims.get("sub"), str) or not claims.get("sub"):
        raise RuntimeError("id_token_audience_invalid")
    if claims.get("nonce") != nonce:
        raise RuntimeError("id_token_nonce_invalid")
    try:
        if float(claims.get("exp", 0)) <= now - 60:
            raise RuntimeError("id_token_expired")
        if float(claims.get("nbf", 0)) > now + 60:
            raise RuntimeError("id_token_not_yet_valid")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("id_token_time_invalid") from exc
    return claims


def _discovery(client: httpx.Client) -> tuple[dict[str, Any], dict[str, Any]]:
    response = client.get(DISCOVERY_URL, timeout=15, follow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"oidc_discovery_http_{response.status_code}")
    metadata = response.json()
    if not isinstance(metadata, dict) or metadata.get("issuer") != ISSUER:
        raise TypeError("oidc_discovery_invalid")
    jwks_uri = metadata.get("jwks_uri")
    parsed = urllib.parse.urlsplit(str(jwks_uri or ""))
    if parsed.scheme != "https" or parsed.hostname != "auth.openai.com":
        raise RuntimeError("oidc_jwks_url_invalid")
    jwks_response = client.get(jwks_uri, timeout=15, follow_redirects=False)
    if jwks_response.status_code != 200:
        raise RuntimeError(f"oidc_jwks_http_{jwks_response.status_code}")
    jwks = jwks_response.json()
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise TypeError("oidc_jwks_invalid")
    return metadata, jwks


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    callback_path = "/auth/callback"
    event: threading.Event
    result: dict[str, Any]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != self.callback_path:
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
        type(self).result.clear()
        type(self).result.update({key: values[0] for key, values in query.items() if values})
        body = (
            "<!doctype html><meta charset=utf-8><title>Arel Bot bağlantısı</title>"
            "<p>Arel Bot için OpenAI bağlantısı tamamlandı. Bu sekmeyi kapatabilirsiniz.</p>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.event.set()

    def log_message(self, format: str, *args: Any) -> None:
        # The callback path includes a one-time authorization code. Never log it.
        return


def _make_server() -> tuple[http.server.HTTPServer, str, _CallbackHandler]:
    event = threading.Event()
    handler_type = type("CallbackHandler", (_CallbackHandler,), {"event": event, "result": {}})
    server = http.server.HTTPServer(("127.0.0.1", 1455), handler_type)
    server.timeout = 0.25
    return server, f"http://127.0.0.1:{server.server_port}/auth/callback", handler_type


def _authorize(directory: Path, credentials_path: Path) -> None:
    if credentials_path.exists():
        raise RuntimeError("credentials_already_exist_use_reauthorization_flow")
    _secure_directory(directory)
    host_id = _host_id(directory)
    try:
        server, redirect_uri, handler_type = _make_server()
    except OSError as exc:
        raise RuntimeError("loopback_callback_unavailable") from exc

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    query = {
        "client_id": "dynamic_agent_client",
        "agent_name_hint": "Arel Bot",
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    url = f"{AUTHORIZATION_URL}?{urllib.parse.urlencode(query)}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print("Brave varsayılan tarayıcıda Arel Bot için resmî OpenAI yetkilendirmesi açılıyor.")
    print("İstenen izinleri inceleyip onayla; bu, Arel Bot'un ChatGPT plan kullanımına izin verir.")
    if not webbrowser.open_new_tab(url):
        server.shutdown()
        raise RuntimeError("system_browser_open_failed")
    try:
        deadline = time.monotonic() + 900
        while not handler_type.event.wait(0.25) and time.monotonic() < deadline:
            pass
        if not handler_type.event.is_set():
            raise RuntimeError("oauth_callback_timeout")
        callback = dict(handler_type.result)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    if callback.get("state") != state:
        raise RuntimeError("oauth_state_mismatch")
    if callback.get("error"):
        safe_error = callback["error"]
        if safe_error == "3p_delegated_access_policy_denied":
            raise RuntimeError("organization_admin_must_allow_application")
        if safe_error not in {"access_denied", "login_required", "temporarily_unavailable"}:
            safe_error = "oauth_authorization_failed"
        raise RuntimeError(safe_error)
    code = callback.get("code")
    client_id = callback.get("client_id")
    if not isinstance(code, str) or not code or not isinstance(client_id, str) or not client_id.startswith("oaiapp_"):
        raise RuntimeError("oauth_registration_incomplete")

    with httpx.Client(timeout=20, follow_redirects=False) as client:
        token_response = client.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": RESOURCE,
            },
            headers={"Accept": "application/json"},
        )
        if token_response.status_code != 200:
            payload = {}
            try:
                payload = token_response.json()
            except (ValueError, httpx.DecodingError):
                payload = {}
            error = payload.get("error") if isinstance(payload, dict) else None
            if error == "invalid_grant":
                raise RuntimeError("oauth_code_expired_start_again")
            raise RuntimeError(f"oauth_token_exchange_http_{token_response.status_code}")
        token_data = token_response.json()
        if not isinstance(token_data, dict):
            raise TypeError("oauth_token_response_invalid")
        scopes = token_data.get("scope")
        if not isinstance(scopes, str):
            raise TypeError("oauth_scope_response_missing")
        granted_scopes = sorted(set(scopes.split()))
        if not REQUIRED_SCOPES.issubset(granted_scopes):
            raise RuntimeError("oauth_required_scope_not_granted")
        id_token = token_data.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise RuntimeError("oauth_id_token_missing")
        metadata, jwks = _discovery(client)
        claims = _verify_id_token(id_token, client_id=client_id, nonce=nonce, metadata=metadata, jwks=jwks)
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        if not isinstance(access_token, str) or not access_token or not isinstance(refresh_token, str) or not refresh_token:
            raise RuntimeError("oauth_token_response_invalid")
        try:
            expires_seconds = max(60, min(86_400, int(expires_in)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("oauth_token_expiry_invalid") from exc

    now = time.time()
    record = {
        "version": 1,
        "issuer": ISSUER,
        "subject": claims["sub"],
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(token_data.get("token_type") or "Bearer"),
        "expires_in": expires_seconds,
        "expires_at": now + expires_seconds,
        "earliest_refresh_at": token_data.get("earliest_refresh_at"),
        "scopes": granted_scopes,
        "saved_at": now,
    }
    _atomic_write_record(credentials_path, record)
    _restrict_windows_acl(credentials_path, directory=False)
    print("OpenAI bağlantısı ve izin kapsamı doğrulandı; oturum bilgileri korumalı yerel dosyaya kaydedildi.")
    _show_models(credentials_path)


def _show_models(credentials_path: Path) -> None:
    async def load_token() -> str:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            credentials = ChatGPTPlanCredentials(credentials_path, client, timeout_seconds=15)
            return await credentials.access_token()

    token = asyncio.run(load_token())
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.get(MODELS_URL, headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise RuntimeError(f"model_catalog_http_{response.status_code}")
        data = response.json()
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise TypeError("model_catalog_invalid")
    visible = []
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") not in {"list", None}:
            continue
        slug = model.get("slug") or model.get("id")
        name = model.get("display_name") or model.get("name") or slug
        if isinstance(slug, str) and isinstance(name, str):
            visible.append((slug, name))
    if not visible:
        raise RuntimeError("no_available_models")
    print("Bu hesapta kullanılabilir modeller:")
    for slug, name in visible[:80]:
        print(f"- {name} ({slug})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Arel Bot için ChatGPT plan bağlantısını kur")
    parser.add_argument("--directory", type=Path, default=_default_dir())
    parser.add_argument("--credentials", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="Kaydedilmiş bağlantıyı ve model kataloğunu denetle")
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    credentials_path = args.credentials.expanduser().absolute() if args.credentials else directory / "credentials.json"
    try:
        if args.check:
            record = _load_record(credentials_path)
            print("Korumalı OpenAI bağlantı kaydı bulundu.")
            print(f"Plan kullanım izni: {'var' if PLAN_SCOPE in record['scopes'] else 'yok'}")
            _show_models(credentials_path)
        else:
            _authorize(directory, credentials_path)
        return 0
    except (RuntimeError, TypeError, ValueError, OSError, httpx.HTTPError) as exc:
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"OpenAI bağlantısı tamamlanamadı: {code[:96]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
