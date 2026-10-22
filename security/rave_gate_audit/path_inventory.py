"""Small, non-destructive path inventory over a fixed candidate list.

GET only, one request per path, no parameters, no payloads. Prints the status
and a short body fingerprint so an endpoint can be told apart from a 404.
"""

from __future__ import annotations

import argparse
import hashlib

import httpx

PATHS = (
    "/",
    "/api",
    "/api/",
    "/api/connect",
    "/api/session",
    "/api/me",
    "/api/user",
    "/api/users",
    "/api/profile",
    "/api/movies",
    "/api/films",
    "/api/items",
    "/api/list",
    "/api/search",
    "/api/config",
    "/api/settings",
    "/api/health",
    "/health",
    "/healthz",
    "/status",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/swagger.json",
    "/api/v1",
    "/graphql",
    "/graphiql",
    "/admin",
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/openid-configuration",
    "/manifest.json",
    "/favicon.ico",
    "/static/",
    "/assets/",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", nargs="+", required=True)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    for target in args.targets:
        print(f"=== {target} ===")
        try:
            with httpx.Client(
                base_url=target,
                timeout=args.timeout,
                follow_redirects=False,
                headers={"User-Agent": "rave-gate-audit/1.0"},
            ) as client:
                for path in PATHS:
                    try:
                        response = client.get(path)
                    except httpx.HTTPError as exc:
                        print(f"  {path:42} ERROR {type(exc).__name__}")
                        continue
                    body = response.content[:400]
                    fingerprint = hashlib.sha256(body).hexdigest()[:8]
                    ctype = response.headers.get("content-type", "")[:32]
                    print(
                        f"  {path:42} {response.status_code:<4} "
                        f"len={len(response.content):<8} {fingerprint}  {ctype}"
                    )
        except httpx.HTTPError as exc:
            print(f"  TARGET ERROR {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
