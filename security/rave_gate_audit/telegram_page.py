"""Read the public Telegram pages for the Mini App and identify its bot."""

from __future__ import annotations

import argparse
import re

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--urls", nargs="+", default=["https://t.me/RaveAppBot", "https://t.me/RaveAppBot/raveapp"])
    args = parser.parse_args()

    for url in args.urls:
        print(f"=== {url} ===")
        try:
            response = httpx.get(url, timeout=25, follow_redirects=True)
        except httpx.HTTPError as exc:
            print(f"  error: {exc}")
            continue
        body = response.text
        print(f"  status : {response.status_code}")
        print(f"  length : {len(body)}")
        title = re.findall(r"<title[^>]*>(.*?)</title>", body, re.S)
        print(f"  title  : {title[:1]}")
        description = re.findall(
            r'<meta property="og:description" content="(.*?)"', body, re.S
        )
        print(f"  ogdesc : {description[:1]}")
        for pattern in ("tgme_page_widget_web_app", "tgme_page_widget_video", "tgme_page_icon"):
            hits = re.findall(pattern + r"[^>]*", body)
            if hits:
                print(f"  {pattern}: {hits[:1]}")
        names = re.findall(r'tgme_page_extra_title[^>]*>([^<]*)', body)
        if names:
            print(f"  extra  : {names[:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
