"""Is this bot token the one the gate actually accepts?

The check is deliberately two-sided, because either side alone can lie:

1. Telegram side - ``getMe`` says which bot the token belongs to and whether
   that bot even has a Main Mini App.
2. Gate side - a payload signed with that token is posted to ``/api/connect``.
   If the gate verified the signature the way the spec says, only the token
   of the bot that owns the Mini App gets ``verified:true``.

Agreement between the two means the token is the gate's. Disagreement means it
is not, and no amount of guessing fixes that.
"""

from __future__ import annotations

import argparse
import json

import httpx

import tg_init_data


def telegram_side(token: str) -> dict:
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=20
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    if response.status_code != 200:
        return {"ok": False, "error": f"HTTP {response.status_code}"}
    payload = response.json()
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("description", "unknown")}
    bot = payload["result"]
    return {
        "ok": True,
        "id": bot.get("id"),
        "username": bot.get("username"),
        "first_name": bot.get("first_name"),
        "has_main_web_app": bot.get("has_main_web_app"),
    }


def gate_side(target: str, token: str) -> dict:
    payload = tg_init_data.build_valid_init_data(token)
    result = {}
    with httpx.Client(base_url=target, timeout=20, follow_redirects=False) as client:
        signed = client.post(
            "/api/connect", json={"init_data": payload, "gate": True}
        )
        result["signed"] = {
            "status": signed.status_code,
            "body": signed.text[:200],
            "verified": _verified(signed),
        }

        tampered = payload[:-1] + ("0" if payload[-1] != "0" else "1")
        broken = client.post(
            "/api/connect", json={"init_data": tampered, "gate": True}
        )
        result["tampered"] = {
            "status": broken.status_code,
            "body": broken.text[:200],
            "verified": _verified(broken),
        }

        empty = client.post("/api/connect", json={"init_data": "", "gate": True})
        result["empty"] = {
            "status": empty.status_code,
            "body": empty.text[:200],
            "verified": _verified(empty),
        }
    return result


def _verified(response: httpx.Response) -> bool:
    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("verified") is True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--target", default="https://film.samildev.com")
    args = parser.parse_args()

    token = "".join(args.token.split())
    print("=== TELEGRAM SIDE ===")
    telegram = telegram_side(token)
    print(json.dumps(telegram, indent=2, ensure_ascii=False))

    print("\n=== GATE SIDE ===")
    gate = gate_side(args.target, token)
    print(json.dumps(gate, indent=2, ensure_ascii=False))

    accepted = gate["signed"]["verified"]
    print("\n=== VERDICT ===")
    if not telegram.get("ok"):
        print("token rejected by Telegram: unusable")
        return 2
    print(f"token belongs to bot     : @{telegram['username']}")
    print(f"bot has main web app     : {telegram['has_main_web_app']}")
    print(f"gate accepted our payload : {accepted}")
    if accepted:
        print("VERDICT: this is the gate's token, the gate accepted our signature")
        return 0
    print("VERDICT: the gate rejected a correctly signed payload from this token")
    print("         -> the token belongs to a different bot, the gate verifies")
    print("            the signature against another bot's token")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
