"""Sweep for a deployment that leaked its own files.

A source tree, a config file or a backup sitting next to the app is the
cheapest way into a service like this, and it also hands over the exact
validation code for review. GET only, fixed candidate list.
"""

from __future__ import annotations

import argparse
import hashlib

import httpx

PATHS = (
    "/.git/HEAD",
    "/.git/config",
    "/.gitignore",
    "/.env",
    "/.env.local",
    "/.env.production",
    "/.vscode/settings.json",
    "/Dockerfile",
    "/docker-compose.yml",
    "/nginx.conf",
    "/requirements.txt",
    "/package.json",
    "/composer.json",
    "/config.json",
    "/config.py",
    "/settings.py",
    "/app.py",
    "/server.py",
    "/main.py",
    "/wsgi.py",
    "/Procfile",
    "/backup.sql",
    "/dump.sql",
    "/database.sql",
    "/db.sqlite3",
    "/data.db",
    "/.DS_Store",
    "/wp-admin/",
    "/.well-known/security.txt",
    "/proxy",
    "/stream",
    "/hls",
    "/m3u8",
    "/media",
    "/video",
    "/embed",
    "/player",
    "/watch",
    "/download",
    "/cdn",
    "/proxy/",
    "/api/proxy",
    "/api/stream",
    "/api/v2",
    "/api/internal",
    "/api/debug",
    "/api/health",
    "/api/providers",
    "/api/config",
    "/api/stats",
    "/api/admin",
    "/admin/api",
    "/internal",
    "/debug",
    "/metrics",
    "/actuator",
    "/server-status",
    "/nginx_status",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="https://film.samildev.com")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    baseline_len = 36
    interesting = []

    with httpx.Client(
        base_url=args.target, timeout=args.timeout, follow_redirects=False,
        headers={"User-Agent": "rave-gate-audit/1.0"},
    ) as client:
        for path in PATHS:
            try:
                response = client.get(path)
            except httpx.HTTPError as exc:
                print(f"  {path:34} ERROR {type(exc).__name__}")
                continue
            length = len(response.content)
            fingerprint = hashlib.sha256(response.content[:200]).hexdigest()[:8]
            note = ""
            if response.status_code == 200:
                note = "  <<< HIT"
                interesting.append((path, response))
            elif response.status_code != 401 and length != baseline_len:
                note = "  (not the blanket 401)"
            print(f"  {path:34} {response.status_code:<4} len={length:<7} {fingerprint}{note}")

    print(f"\n=== hits: {len(interesting)} ===")
    for path, response in interesting:
        print(f"\n--- {path} ({len(response.content)} bytes) ---")
        print(response.text[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
