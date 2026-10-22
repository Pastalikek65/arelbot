"""Rave Mini App gate audit battery.

Runs a fixed battery of HTTP checks against the ``/api/connect`` gate and
reports one verdict per check. Every check answers a single question about
whether the server, not the browser, is doing the authentication.

Usage
-----
    # local lab
    python audit.py --target http://127.0.0.1:8099

    # a host you own / are authorised to test
    python audit.py --target https://example.com --bot-token <TOKEN> --ack-authorized

Checks needing a bot token (replay, staleness, wrong-token signature) are
skipped when no token is supplied; everything else always runs.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import socket
import ssl
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

import tg_init_data

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"

SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INFO: 4}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


@dataclass
class Check:
    id: str
    title: str
    severity: str
    passed: bool
    expected: str
    observed: str
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"


@dataclass
class AuditResult:
    target: str
    started_at: float = field(default_factory=time.time)
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and not c.skipped]

    @property
    def blocking(self) -> list[Check]:
        return [c for c in self.failures if c.severity in (CRITICAL, HIGH)]

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "duration_s": round(time.time() - self.started_at, 2),
            "passed": len([c for c in self.checks if c.passed and not c.skipped]),
            "failed": len(self.failures),
            "skipped": len([c for c in self.checks if c.skipped]),
            "blocking": len(self.blocking),
            "checks": [asdict(c) for c in self.checks],
        }


LEAK_MARKERS = (
    "traceback",
    "stack trace",
    'file "',
    "sqlstate",
    "psycopg2",
    "pymongo",
    "werkzeug",
    "django.core",
    "internal server error",
)


class Auditor:
    def __init__(
        self,
        target: str,
        bot_token: str | None = None,
        timeout: float = 12.0,
        rate_gap: float = 0.2,
        client: httpx.Client | None = None,
        skip_tls: bool = False,
        captured_init_data: str | None = None,
    ) -> None:
        self.target = target.rstrip("/")
        self.bot_token = bot_token
        self.timeout = timeout
        self.rate_gap = rate_gap
        self.skip_tls = skip_tls
        self.captured_init_data = captured_init_data
        self._client = client
        self._own_client = client is None
        self._xff_counter = 0
        self.control_ok: bool | None = None
        self.control_reason: str = ""

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.target,
                timeout=self.timeout,
                follow_redirects=False,
                headers={"User-Agent": "rave-gate-audit/1.0"},
            )
        return self._client

    def close(self) -> None:
        if self._own_client and self._client is not None:
            self._client.close()

    # ---------------------------------------------------------------- helpers
    def next_xff(self) -> str:
        """Unique client IP per check so rate-limit buckets do not collide."""
        self._xff_counter += 1
        return f"10.99.{self._xff_counter // 250}.{self._xff_counter % 250 + 1}"

    def _post(self, path: str, payload: dict, headers: dict | None = None, xff: str | None = None):
        time.sleep(self.rate_gap)
        merged = dict(headers or {})
        if xff:
            merged["X-Forwarded-For"] = xff
        return self.client.post(path, json=payload, headers=merged)

    def _post_raw(self, path: str, body: bytes, content_type: str, xff: str | None = None):
        time.sleep(self.rate_gap)
        headers = {"Content-Type": content_type}
        if xff:
            headers["X-Forwarded-For"] = xff
        return self.client.post(path, content=body, headers=headers)

    @staticmethod
    def _verified(response: httpx.Response) -> bool:
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and data.get("verified") is True

    @staticmethod
    def _body_text(response: httpx.Response) -> str:
        return response.text.lower()

    # ----------------------------------------------------------------- checks
    def check_positive_control(self, result: AuditResult) -> None:
        """A fresh, correctly signed payload must be accepted.

        Without this control every later check can pass for the wrong reason:
        a server that demands the Ed25519 ``signature`` field rejects my
        hash-only payloads too, and staleness or replay then looks proven.
        """
        if not self.bot_token:
            result.add(
                Check(
                    id="GATE-00",
                    title="Positive control: fresh signed payload accepted",
                    severity=HIGH,
                    passed=True,
                    expected="requires --bot-token",
                    observed="no bot token supplied",
                    skipped=True,
                )
            )
            return

        payload = tg_init_data.build_valid_init_data(self.bot_token)
        response = self._post("/api/connect", {"init_data": payload, "gate": True}, xff=self.next_xff())
        accepted = self._verified(response)
        self.control_ok = accepted
        self.control_reason = self._reason(response)
        if accepted:
            detail = "fresh payload accepted"
            passed = True
        else:
            detail = (
                f"fresh payload rejected (HTTP {response.status_code}, "
                f"reason={self.control_reason or 'unknown'}) - staleness and "
                "replay cannot be judged from this run"
            )
            passed = False
        result.add(
            Check(
                id="GATE-00",
                title="Positive control: fresh signed payload accepted",
                severity=HIGH,
                passed=passed,
                expected="a freshly signed init_data opens the gate",
                observed=detail,
            )
        )

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            return ""
        if isinstance(data, dict):
            for key in ("error", "reason", "detail"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    def check_direct_api_without_init_data(self, result: AuditResult) -> None:
        response = self._post("/api/connect", {"init_data": "", "gate": True}, xff=self.next_xff())
        passed = not self._verified(response)
        result.add(
            Check(
                id="GATE-01",
                title="API reachable without any init_data",
                severity=CRITICAL,
                passed=passed,
                expected="no verified session without init_data",
                observed=f"HTTP {response.status_code}, verified={self._verified(response)}",
            )
        )

    def check_missing_hash(self, result: AuditResult) -> None:
        init_data = tg_init_data.encode(
            {"auth_date": str(int(time.time())), "user": '{"id":1,"first_name":"audit"}'}
        )
        response = self._post("/api/connect", {"init_data": init_data, "gate": True}, xff=self.next_xff())
        passed = not self._verified(response)
        result.add(
            Check(
                id="GATE-02",
                title="Payload without hash accepted",
                severity=CRITICAL,
                passed=passed,
                expected="unsigned payload rejected",
                observed=f"HTTP {response.status_code}, verified={self._verified(response)}",
            )
        )

    def check_wrong_token_signature(self, result: AuditResult) -> None:
        if not self.bot_token:
            result.add(
                Check(
                    id="GATE-03",
                    title="Forged signature from wrong bot token",
                    severity=CRITICAL,
                    passed=True,
                    expected="requires --bot-token",
                    observed="no bot token supplied",
                    skipped=True,
                )
            )
            return
        forged = tg_init_data.build_valid_init_data("999999:WRONG-TOKEN-ATTEMPT")
        response = self._post("/api/connect", {"init_data": forged, "gate": True}, xff=self.next_xff())
        passed = not self._verified(response)
        result.add(
            Check(
                id="GATE-03",
                title="Forged signature from wrong bot token",
                severity=CRITICAL,
                passed=passed,
                expected="signature bound to the real bot token",
                observed=f"HTTP {response.status_code}, verified={self._verified(response)}",
            )
        )

    def check_gate_flag_trust(self, result: AuditResult) -> None:
        junk = tg_init_data.encode({"auth_date": "1", "user": "{}", "hash": "deadbeef"})
        response = self._post("/api/connect", {"init_data": junk, "gate": True}, xff=self.next_xff())
        passed = not self._verified(response)
        result.add(
            Check(
                id="GATE-04",
                title="Client-side gate flag trusted by the server",
                severity=CRITICAL,
                passed=passed,
                expected="gate:true alone grants nothing",
                observed=f"HTTP {response.status_code}, verified={self._verified(response)}",
            )
        )

    def check_stale_auth_date(self, result: AuditResult) -> None:
        if not self.bot_token:
            result.add(
                Check(
                    id="GATE-05",
                    title="Stale init_data accepted",
                    severity=HIGH,
                    passed=True,
                    expected="requires --bot-token",
                    observed="no bot token supplied",
                    skipped=True,
                )
            )
            return
        if self.control_ok is False:
            result.add(
                Check(
                    id="GATE-05",
                    title="Stale init_data accepted",
                    severity=HIGH,
                    passed=True,
                    expected="a fresh signed payload is accepted",
                    observed=f"control failed ({self.control_reason or 'rejected'}), check inconclusive",
                    skipped=True,
                )
            )
            return
        stale = tg_init_data.build_valid_init_data(
            self.bot_token, auth_date=int(time.time()) - 60 * 60 * 24 * 7
        )
        response = self._post("/api/connect", {"init_data": stale, "gate": True}, xff=self.next_xff())
        passed = not self._verified(response)
        result.add(
            Check(
                id="GATE-05",
                title="Stale init_data accepted",
                severity=HIGH,
                passed=passed,
                expected="init_data older than the freshness window rejected",
                observed=f"HTTP {response.status_code}, verified={self._verified(response)}",
            )
        )

    def check_replay(self, result: AuditResult) -> None:
        if not self.bot_token:
            result.add(
                Check(
                    id="GATE-06",
                    title="Signed init_data replayable",
                    severity=HIGH,
                    passed=True,
                    expected="requires --bot-token",
                    observed="no bot token supplied",
                    skipped=True,
                )
            )
            return
        if self.control_ok is False:
            result.add(
                Check(
                    id="GATE-06",
                    title="Signed init_data replayable",
                    severity=HIGH,
                    passed=True,
                    expected="a fresh signed payload is accepted",
                    observed=f"control failed ({self.control_reason or 'rejected'}), check inconclusive",
                    skipped=True,
                )
            )
            return
        valid = tg_init_data.build_valid_init_data(self.bot_token)
        xff = self.next_xff()
        first = self._post("/api/connect", {"init_data": valid, "gate": True}, xff=xff)
        second = self._post("/api/connect", {"init_data": valid, "gate": True}, xff=xff)
        accepted_once = self._verified(first)
        accepted_twice = self._verified(second)
        passed = (not accepted_once) or (not accepted_twice)
        result.add(
            Check(
                id="GATE-06",
                title="Signed init_data replayable",
                severity=HIGH,
                passed=passed,
                expected="a signed payload is not accepted twice",
                observed=f"first={accepted_once} second={accepted_twice}",
            )
        )

    @contextlib.contextmanager
    def _anon_client(self):
        """A cookie-less client on the same transport as the main one."""
        transport = getattr(self.client, "_transport", None)
        kwargs = {
            "base_url": str(self.client.base_url),
            "timeout": self.timeout,
            "follow_redirects": False,
            "headers": {"User-Agent": "rave-gate-audit/1.0"},
        }
        if transport is not None:
            kwargs["transport"] = transport
        with httpx.Client(**kwargs) as anon:
            yield anon

    def check_captured_payload(self, result: AuditResult) -> None:
        """Replay a genuine payload captured from a real Telegram session.

        A captured ``init_data`` is the only way to test the freshness window
        and the replay cache without holding the bot token: it carries a real
        signature, and by the time it is replayed it is already old.
        """
        if not self.captured_init_data:
            for check_id, title in (
                ("GATE-14", "Captured payload still accepted after capture"),
                ("GATE-15", "Captured payload accepted a second time"),
            ):
                result.add(
                    Check(
                        id=check_id,
                        title=title,
                        severity=HIGH,
                        passed=True,
                        expected="requires --captured-init-data",
                        observed="no captured payload supplied",
                        skipped=True,
                    )
                )
            return

        payload = self.captured_init_data
        xff = self.next_xff()
        first = self._post("/api/connect", {"init_data": payload, "gate": True}, xff=xff)
        second = self._post("/api/connect", {"init_data": payload, "gate": True}, xff=xff)
        accepted_first = self._verified(first)
        accepted_second = self._verified(second)

        result.add(
            Check(
                id="GATE-14",
                title="Captured payload still accepted after capture",
                severity=HIGH,
                passed=not accepted_first,
                expected="a payload captured in the past is refused",
                observed=f"first_use_accepted={accepted_first} (HTTP {first.status_code})",
            )
        )
        result.add(
            Check(
                id="GATE-15",
                title="Captured payload accepted a second time",
                severity=HIGH,
                passed=not accepted_second,
                expected="one signed payload opens one session, once",
                observed=f"second_use_accepted={accepted_second} (HTTP {second.status_code})",
            )
        )

    def check_application_is_gated(self, result: AuditResult) -> None:
        """The app shell must not be served to an unauthenticated caller.

        The request goes out on a cookie-less client so an earlier successful
        verification cannot colour the result.
        """
        try:
            with self._anon_client() as anon:
                response = anon.get("/")
        except httpx.HTTPError as exc:
            result.add(
                Check(
                    id="GATE-12",
                    title="Application content served without authentication",
                    severity=HIGH,
                    passed=False,
                    expected="the app shell only after a verified session",
                    observed=f"request failed: {exc}",
                )
            )
            return

        body = response.text
        gate_markers = ("Dogrulaniyor", "Telegram uzerinden acin", "pf_gate")
        served_gate = any(marker in body for marker in gate_markers)
        result.add(
            Check(
                id="GATE-12",
                title="Application content served without authentication",
                severity=HIGH,
                passed=served_gate,
                expected="an anonymous caller gets the gate, not the app",
                observed=(
                    "gate page served to an anonymous caller"
                    if served_gate
                    else f"anonymous caller received app content (HTTP {response.status_code}, "
                    f"{len(body)} bytes)"
                ),
            )
        )

    def check_anonymous_api_routes(self, result: AuditResult) -> None:
        """Inventory of API routes that answer an anonymous caller."""
        candidates = (
            "/api/session",
            "/api/me",
            "/api/user",
            "/api/profile",
            "/api/config",
            "/api/settings",
            "/api/health",
            "/api/feed",
        )
        reachable = []
        try:
            with self._anon_client() as anon:
                for path in candidates:
                    try:
                        response = anon.get(path)
                    except httpx.HTTPError:
                        continue
                    if response.status_code < 400:
                        reachable.append(f"{path} -> {response.status_code}")
        except httpx.HTTPError as exc:
            result.add(
                Check(
                    id="GATE-13",
                    title="Anonymous API surface inventory",
                    severity=INFO,
                    passed=True,
                    expected="informational",
                    observed=f"probe failed: {exc}",
                )
            )
            return

        result.add(
            Check(
                id="GATE-13",
                title="Anonymous API surface inventory",
                severity=INFO,
                passed=True,
                expected="informational",
                observed=", ".join(reachable) if reachable else "no common route answered anonymously",
            )
        )

    def check_error_handling(self, result: AuditResult) -> None:
        xff = self.next_xff()
        responses = [
            self._post_raw("/api/connect", b"{not json", "application/json", xff=xff),
            self._post_raw("/api/connect", b'{"init_data": 12345}', "application/json", xff=xff),
            self._post_raw("/api/connect", b'{"init_data": "' + b"A" * 50 + b'"}', "application/json", xff=xff),
            self._post("/api/connect", {"init_data": "auth_date=x&user=%7Bbad", "gate": True}, xff=xff),
        ]
        server_errors = [r for r in responses if r.status_code >= 500]
        passed = not server_errors
        result.add(
            Check(
                id="GATE-07",
                title="Malformed input produces a 5xx",
                severity=MEDIUM,
                passed=passed,
                expected="4xx responses only",
                observed="statuses: " + ", ".join(str(r.status_code) for r in responses),
            )
        )

    def check_oversized_body(self, result: AuditResult) -> None:
        payload = b'{"init_data": "' + b"A" * (3 * 1024 * 1024) + b'"}'
        response = self._post_raw("/api/connect", payload, "application/json", xff=self.next_xff())
        passed = response.status_code < 500
        result.add(
            Check(
                id="GATE-08",
                title="Oversized body handling",
                severity=MEDIUM,
                passed=passed,
                expected="4xx rejection instead of a crash",
                observed=f"HTTP {response.status_code}",
            )
        )

    def check_error_body_leak(self, result: AuditResult) -> None:
        response = self._post("/api/connect", {"init_data": "garbage", "gate": True}, xff=self.next_xff())
        body = self._body_text(response)
        hits = [m for m in LEAK_MARKERS if m in body]
        passed = not hits
        result.add(
            Check(
                id="GATE-09",
                title="Error response leaks internals",
                severity=MEDIUM,
                passed=passed,
                expected="no stack trace or framework internals in the body",
                observed=f"markers: {hits or 'none'}",
            )
        )

    def check_rate_limiting(self, result: AuditResult) -> None:
        xff = self.next_xff()
        statuses = []
        for _ in range(10):
            response = self._post("/api/connect", {"init_data": "bad", "gate": True}, xff=xff)
            statuses.append(response.status_code)
        limited = any(s == 429 for s in statuses)
        result.add(
            Check(
                id="GATE-10",
                title="No lockout or throttle on repeated auth failures",
                severity=MEDIUM,
                passed=limited,
                expected="429 after repeated failed verifications",
                observed="statuses: " + ", ".join(str(s) for s in statuses),
            )
        )

    def check_rate_limit_rotation(self, result: AuditResult) -> None:
        """A throttle keyed on a spoofable header can be walked around."""
        fixed_ip = self.next_xff()
        fixed = [
            self._post("/api/connect", {"init_data": "bad", "gate": True}, xff=fixed_ip).status_code
            for _ in range(10)
        ]
        rotating = [
            self._post(
                "/api/connect", {"init_data": "bad", "gate": True}, xff=self.next_xff()
            ).status_code
            for _ in range(10)
        ]
        limited_fixed = any(s == 429 for s in fixed)
        limited_rotating = any(s == 429 for s in rotating)
        passed = limited_fixed and limited_rotating
        result.add(
            Check(
                id="GATE-11",
                title="Throttle bypassed by rotating the forwarded client identity",
                severity=HIGH,
                passed=passed,
                expected="the throttle still bites when the client identity changes",
                observed=(
                    f"same_identity_limited={limited_fixed} "
                    f"rotated_identity_limited={limited_rotating}"
                ),
            )
        )

    def check_security_headers(self, result: AuditResult) -> None:
        response = self.client.get("/")
        headers = {k.lower(): v for k, v in response.headers.items()}
        required = {
            "strict-transport-security": LOW,
            "content-security-policy": MEDIUM,
            "x-content-type-options": LOW,
            "x-frame-options": MEDIUM,
            "referrer-policy": LOW,
        }
        missing = [name for name in required if name not in headers]
        severity = max((required[m] for m in missing), key=lambda s: SEVERITY_ORDER[s]) if missing else LOW
        result.add(
            Check(
                id="HTTP-01",
                title="Security headers on the gate page",
                severity=severity,
                passed=not missing,
                expected="HSTS, CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy",
                observed="missing: " + (", ".join(missing) if missing else "none"),
            )
        )

    def check_cors(self, result: AuditResult) -> None:
        response = self._post(
            "/api/connect",
            {"init_data": "garbage", "gate": True},
            headers={"Origin": "https://evil.example"},
            xff=self.next_xff(),
        )
        allow_origin = response.headers.get("access-control-allow-origin", "")
        allow_credentials = response.headers.get("access-control-allow-credentials", "")
        reflected = allow_origin == "https://evil.example"
        wildcard_with_credentials = allow_origin == "*" and bool(allow_credentials)
        passed = not (reflected or wildcard_with_credentials)
        result.add(
            Check(
                id="HTTP-02",
                title="CORS reflects arbitrary origins on the auth endpoint",
                severity=MEDIUM,
                passed=passed,
                expected="no arbitrary origin reflection, no wildcard with credentials",
                observed=(
                    f"allow-origin={allow_origin or 'absent'} "
                    f"allow-credentials={allow_credentials or 'absent'}"
                ),
            )
        )

    def check_unsafe_methods(self, result: AuditResult) -> None:
        response = self.client.request("TRACE", "/")
        passed = response.status_code not in (200, 204)
        result.add(
            Check(
                id="HTTP-03",
                title="TRACE method enabled",
                severity=LOW,
                passed=passed,
                expected="405 for TRACE",
                observed=f"HTTP {response.status_code}",
            )
        )

    def check_tls(self, result: AuditResult) -> None:
        parsed = urlparse(self.target)
        host = parsed.hostname or ""
        if self.skip_tls or host in LOCAL_HOSTS:
            result.add(
                Check(
                    id="TLS-01",
                    title="TLS certificate valid and unexpired",
                    severity=HIGH,
                    passed=True,
                    expected="skipped for local targets",
                    observed="local target, check skipped",
                    skipped=True,
                )
            )
            return
        if parsed.scheme != "https":
            result.add(
                Check(
                    id="TLS-01",
                    title="TLS certificate valid and unexpired",
                    severity=HIGH,
                    passed=False,
                    expected="https endpoint",
                    observed=f"scheme={parsed.scheme or 'none'}",
                )
            )
            return
        try:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(
                socket.create_connection((host, parsed.port or 443), timeout=8),
                server_hostname=host,
            ) as sock:
                cert = sock.getpeercert()
        except (ssl.SSLError, OSError) as exc:
            result.add(
                Check(
                    id="TLS-01",
                    title="TLS certificate valid and unexpired",
                    severity=HIGH,
                    passed=False,
                    expected="valid certificate chain",
                    observed=f"TLS error: {exc}",
                )
            )
            return

        not_after = cert.get("notAfter")
        expires = ""
        expired = False
        if not_after:
            try:
                expires_at = dt.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=dt.timezone.utc
                )
                expired = expires_at < dt.datetime.now(dt.timezone.utc)
                expires = expires_at.isoformat()
            except ValueError:
                expires = not_after
        result.add(
            Check(
                id="TLS-01",
                title="TLS certificate valid and unexpired",
                severity=HIGH,
                passed=not expired,
                expected="certificate not expired",
                observed=f"notAfter={expires}",
            )
        )

    # ------------------------------------------------------------------- run
    def run(self) -> AuditResult:
        result = AuditResult(target=self.target)
        self.check_positive_control(result)
        self.check_direct_api_without_init_data(result)
        self.check_missing_hash(result)
        self.check_wrong_token_signature(result)
        self.check_gate_flag_trust(result)
        self.check_stale_auth_date(result)
        self.check_replay(result)
        self.check_captured_payload(result)
        self.check_error_handling(result)
        self.check_oversized_body(result)
        self.check_error_body_leak(result)
        self.check_rate_limiting(result)
        self.check_rate_limit_rotation(result)
        self.check_application_is_gated(result)
        self.check_anonymous_api_routes(result)
        self.check_security_headers(result)
        self.check_cors(result)
        self.check_unsafe_methods(result)
        self.check_tls(result)
        return result


def render(result: AuditResult) -> str:
    lines = [
        f"Rave gate audit - {result.target}",
        "=" * 72,
    ]
    for check in result.checks:
        lines.append(
            f"[{check.status:4}] {check.id}  {check.title}  "
            f"(severity if failing: {check.severity})"
        )
        lines.append(f"        expected: {check.expected}")
        lines.append(f"        observed: {check.observed}")
    lines.append("=" * 72)
    passed = len([c for c in result.checks if c.passed and not c.skipped])
    skipped = len([c for c in result.checks if c.skipped])
    lines.append(
        f"passed={passed} failed={len(result.failures)} skipped={skipped} "
        f"blocking={len(result.blocking)}"
    )
    if result.failures:
        lines.append("findings:")
        for check in sorted(result.failures, key=lambda c: SEVERITY_ORDER[c.severity]):
            lines.append(f"  - [{check.severity}] {check.id} {check.title}: {check.observed}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a Rave Mini App gate endpoint")
    parser.add_argument("--target", required=True, help="base URL of the deployment")
    parser.add_argument("--bot-token", default=None, help="bot token for signature-level checks")
    parser.add_argument(
        "--captured-init-data",
        default=None,
        help="a genuine init_data captured from a Telegram session, for replay testing",
    )
    parser.add_argument(
        "--captured-file",
        default=None,
        help="path to a file holding a captured init_data",
    )
    parser.add_argument("--ack-authorized", action="store_true", help="confirm you own the target")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    host = urlparse(args.target).hostname or ""
    local = host in LOCAL_HOSTS
    if not local and not args.ack_authorized:
        parser.error(
            "remote targets need --ack-authorized, confirming you own or are "
            "authorised to test the deployment"
        )

    captured = args.captured_init_data
    if args.captured_file:
        captured = Path(args.captured_file).read_text(encoding="utf-8").strip()

    auditor = Auditor(
        args.target,
        bot_token=args.bot_token,
        timeout=args.timeout,
        captured_init_data=captured,
    )
    try:
        result = auditor.run()
    finally:
        auditor.close()

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render(result))
    return 1 if result.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
