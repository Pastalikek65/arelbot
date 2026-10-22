"""Pull the public homepage and report anything that names the app or a repo."""

from __future__ import annotations

import argparse
import re

import httpx

KEYWORDS = ("pixelfilm", "film", "samildev", "github", "gitlab", "api", "telegram", "bot")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://samildev.com")
    args = parser.parse_args()

    response = httpx.get(args.url, timeout=25, follow_redirects=True)
    body = response.text
    print(f"url={args.url} status={response.status_code} len={len(body)}")

    titles = re.findall(r"<title[^>]*>(.*?)</title>", body, re.S)
    print("title:", titles[:3])

    for keyword in KEYWORDS:
        hits = [m.start() for m in re.finditer(re.escape(keyword), body, re.I)]
        if hits:
            print(f"\n=== '{keyword}' x{len(hits)} ===")
            for pos in hits[:6]:
                snippet = re.sub(r"\s+", " ", body[max(0, pos - 90) : pos + 110])
                print(f"  ...{snippet}...")

    links = re.findall(r'href=["\']([^"\']{3,160})["\']', body)
    interesting = sorted(
        {link for link in links if any(k in link.lower() for k in ("samildev", "github", "pixelfilm", "film", "t.me", "bot"))}
    )
    print(f"\n=== interesting links ({len(interesting)}) ===")
    for link in interesting[:60]:
        print(f"  {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
