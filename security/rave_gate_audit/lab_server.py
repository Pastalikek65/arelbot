"""Local replica of the Rave Mini App gate, used to prove the audit battery works.

Mirrors the shape observed on the deployed gate:

* ``GET /``            -> client-side gate page that posts to ``/api/connect``
* ``POST /api/connect`` -> validates ``init_data`` and sets a session cookie

Vulnerability toggles via the ``RAVE_VULN`` environment variable (comma
separated). The lab ships every flaw the battery knows how to detect, so the
test-suite can prove that a green run means something.

    ok              - correct validation (no findings expected)
    no_hash_check   - accepts any payload, never compares the HMAC
    no_auth_date    - skips the freshness window
    no_replay       - no replay cache, a signed payload is reusable forever
    trust_gate      - trusts the client sent ``gate: true`` flag
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, MutableSet

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import tg_init_data

BOT_TOKEN = os.environ.get("RAVE_BOT_TOKEN", "123456:TEST-TOKEN-FOR-LOCAL-LAB")
VULNS = {
    part.strip()
    for part in os.environ.get("RAVE_VULN", "ok").split(",")
    if part.strip()
}
MAX_AGE = int(os.environ.get("RAVE_MAX_AGE", "3600"))

app = FastAPI(title="Rave gate lab")
_replay_seen: MutableSet[str] = set()
_sessions: set[str] = set()
_failures: dict[str, Deque[float]] = defaultdict(deque)
RATE_LIMIT = int(os.environ.get("RAVE_RATE_LIMIT", "5"))  # failures per window
RATE_WINDOW = float(os.environ.get("RAVE_RATE_WINDOW", "10"))

INDEX_HTML = Path(__file__).with_name("lab_index.html")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _client_key(request: Request) -> str:
    """Key the throttle on the forwarded client, the way an nginx front does."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _throttled(key: str) -> bool:
    now = time.time()
    bucket = _failures[key]
    while bucket and now - bucket[0] > RATE_WINDOW:
        bucket.popleft()
    if RATE_LIMIT and len(bucket) >= RATE_LIMIT:
        return True
    bucket.append(now)
    return False


@app.post("/api/connect")
async def connect(request: Request) -> Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return JSONResponse({"verified": False, "error": "bad_request"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"verified": False, "error": "bad_request"}, status_code=400)

    key = _client_key(request)
    if _throttled(key):
        return JSONResponse({"verified": False, "error": "rate_limited"}, status_code=429)

    init_data = body.get("init_data")
    if init_data is not None and not isinstance(init_data, str):
        return JSONResponse({"verified": False, "error": "bad_request"}, status_code=400)
    init_data = init_data or ""
    gate_flag = body.get("gate") is True

    if "no_hash_check" in VULNS:
        result = tg_init_data.ValidationResult(
            ok=bool(init_data), reason="hash_check_disabled", params=tg_init_data.parse(init_data)
        )
    else:
        result = tg_init_data.validate(
            init_data,
            BOT_TOKEN,
            max_age=0 if "no_auth_date" in VULNS else MAX_AGE,
            replay_seen=None if "no_replay" in VULNS else _replay_seen,
        )

    if not result.ok and "trust_gate" in VULNS and gate_flag and init_data:
        result = tg_init_data.ValidationResult(
            ok=True, reason="gate_flag_trusted", params=result.params
        )

    if not result.ok:
        return JSONResponse({"verified": False, "error": result.reason}, status_code=401)

    session = f"sess-{int(time.time() * 1000)}-{len(_sessions)}"
    _sessions.add(session)
    response = JSONResponse({"verified": True, "user_id": result.user.get("id")})
    response.set_cookie("rave_session", session, httponly=True, samesite="lax")
    return response


@app.get("/api/session")
def session(request: Request) -> Response:
    cookie = request.cookies.get("rave_session")
    if cookie and cookie in _sessions:
        return JSONResponse({"authenticated": True})
    return JSONResponse({"authenticated": False}, status_code=401)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "vulns": sorted(VULNS)}
