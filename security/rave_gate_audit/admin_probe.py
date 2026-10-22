"""Map the access check on the admin route.

The route answers a uniform ``403 <h1>Yetkisiz</h1>`` for every request so far.
This sweep looks for a credential shape the route actually accepts, and for a
header the route trusts. No password dictionaries, no brute force: the point is
to learn which *kind* of credential is consulted.
"""

from __future__ import annotations

import argparse

import httpx

HEADERS = (
    {"X-Admin-Token": "1"},
    {"X-Admin-Key": "1"},
    {"X-Admin-Secret": "1"},
    {"X-Api-Key": "1"},
    {"X-Access-Key": "1"},
    {"X-Auth-Token": "1"},
    {"X-Secret": "1"},
    {"X-Token": "1"},
    {"X-Admin": "1"},
    {"X-Internal": "1"},
    {"X-Internal-Token": "1"},
    {"X-Service-Token": "1"},
    {"X-Gate-Key": "1"},
    {"X-Pixelfilm-Key": "1"},
    {"Authorization": "Bearer 1"},
    {"Authorization": "Token 1"},
    {"Authorization": "Basic YWRtaW46"},
)

COOKIES = (
    {"admin_token": "1"},
    {"admin_key": "1"},
    {"admin": "1"},
    {"admin_session": "1"},
    {"pixelfilm_admin": "1"},
    {"pf_admin": "1"},
    {"key": "1"},
)

QUERIES = (
    "token=1",
    "key=1",
    "admin_key=1",
    "admin_token=1",
    "secret=1",
    "auth=1",
    "access_key=1",
    "api_key=1",
)

PATHS = ("/admin", "/admin/panel", "/admin-panel", "/administrator", "/yonetim")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="https://film.samildev.com")
    parser.add_argument("--path", default="/admin")
    args = parser.parse_args()

    baseline = None
    deviations = []

    with httpx.Client(
        base_url=args.target, timeout=15, follow_redirects=False,
        headers={"User-Agent": "rave-gate-audit/1.0"},
    ) as client:
        baseline = client.get(args.path)
        print(f"baseline {args.path} -> {baseline.status_code} {baseline.text[:60]}")
        base_signature = (baseline.status_code, baseline.text)

        def probe(label: str, response: httpx.Response) -> None:
            signature = (response.status_code, response.text)
            flag = "" if signature == base_signature else "  <<< DEVIATION"
            if signature != base_signature:
                deviations.append(label)
            print(f"  {label:46} {response.status_code:<4} len={len(response.content):<6}{flag}")

        print("=== HEADER SHAPES ===")
        for headers in HEADERS:
            probe(", ".join(f"{k}: {v}" for k, v in headers.items()), client.get(args.path, headers=headers))

        print("=== COOKIE SHAPES ===")
        for cookies in COOKIES:
            probe(", ".join(f"{k}={v}" for k, v in cookies.items()), client.get(args.path, cookies=cookies))

        print("=== QUERY SHAPES ===")
        for query in QUERIES:
            probe(query, client.get(f"{args.path}?{query}"))

        print("=== NEARBY ADMIN PATHS ===")
        for path in PATHS:
            probe(path, client.get(path))

    print(f"\ndeviations: {len(deviations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
