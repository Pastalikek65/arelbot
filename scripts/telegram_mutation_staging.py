#!/usr/bin/env python3
"""Run an explicit, bounded Telegram mutation staging probe.

This command is intentionally harder to invoke than a read-only probe. It
requires ``--live``, ``--confirm-live``, ``--allow-mutation`` and
``ARELBOT_TELEGRAM_MUTATION_LIVE=1``. A staging chat id is always explicit;
forum, video, sticker, callback and file operations additionally require the
operator to provide the corresponding staging identifiers. No identifiers,
message text, token or API response is written to the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.production_preflight import read_dotenv
from src.telegram_request import build_bot


class MutationProbeError(ValueError):
    """Stable operator-facing mutation probe error."""


_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
_INT_RE = re.compile(r"^-?\d{1,20}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(?:token|cookie|password|authorization|api[_-]?key|private[_-]?key|secret)")


def validate_live_gate(*, live: bool, confirm_live: bool, allow_mutation: bool, env_value: str | None) -> None:
    if not (live and confirm_live and allow_mutation and str(env_value or "").strip() == "1"):
        raise MutationProbeError("mutation_live_gate_required")


def parse_integer(value: str | None, *, required: bool, label: str) -> int | None:
    raw = str(value or "").strip()
    if not raw and not required:
        return None
    if not _INT_RE.fullmatch(raw):
        raise MutationProbeError(f"{label}_invalid")
    parsed = int(raw)
    if parsed == 0 or abs(parsed) > 9_223_372_036_854_775_807:
        raise MutationProbeError(f"{label}_invalid")
    return parsed


def validate_mutation_inputs(
    *,
    chat_id: int,
    thread_id: int | None,
    video_file_id: str | None,
    sticker_file_id: str | None,
    file_id: str | None,
    callback_query_id: str | None,
) -> None:
    """Validate the complete mutation contract before opening Telegram.

    The live probe deliberately performs several mutations.  Discovering a
    missing file/callback id after ``sendMessage`` would leave a partially
    executed canary in the staging chat, so all opaque Telegram identifiers
    are checked up front and the probe fails closed before constructing a bot.
    """

    if not isinstance(chat_id, int) or chat_id == 0:
        raise MutationProbeError("chat_id_invalid")
    if thread_id is not None and (not isinstance(thread_id, int) or thread_id == 0):
        raise MutationProbeError("thread_id_invalid")
    required = {
        "video_file_id": video_file_id,
        "sticker_file_id": sticker_file_id,
        "file_id": file_id,
        "callback_query_id": callback_query_id,
    }
    for label, value in required.items():
        raw = str(value or "").strip()
        if not raw:
            raise MutationProbeError(f"{label}_required")
        if not _OPAQUE_ID_RE.fullmatch(raw):
            raise MutationProbeError(f"{label}_invalid")


def _safe_error(exc: BaseException) -> str:
    return type(exc).__name__[:80] or "Exception"


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise MutationProbeError("report_target_symlink")
    for parent in (path.parent, *path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise MutationProbeError("report_parent_symlink")
    if _contains_sensitive(payload):
        raise MutationProbeError("report_secret_material")
    parent = path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered == "secret_free" and isinstance(child, bool):
                continue
            if _SENSITIVE_KEY_RE.search(lowered) and child not in (None, False, "", 0):
                return True
            if _contains_sensitive(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    if isinstance(value, str):
        return bool(re.search(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", value) or "authorization:" in value.casefold())
    return False


async def _attempt(operations: dict[str, bool], errors: dict[str, str], name: str, call) -> Any | None:
    try:
        result = await call()
    except Exception as exc:
        operations[name] = False
        errors[name] = _safe_error(exc)
        return None
    operations[name] = True
    return result


async def run_live(
    token: str,
    *,
    chat_id: int,
    thread_id: int | None,
    video_file_id: str | None,
    sticker_file_id: str | None,
    file_id: str | None,
    callback_query_id: str | None,
) -> dict[str, Any]:
    """Execute the fixed mutation contract and return secret-free evidence."""

    # Do this before build_bot/sendMessage.  A live staging run must never
    # create a partial canary merely because a later operation lacks an id.
    validate_mutation_inputs(
        chat_id=chat_id,
        thread_id=thread_id,
        video_file_id=video_file_id,
        sticker_file_id=sticker_file_id,
        file_id=file_id,
        callback_query_id=callback_query_id,
    )
    bot = build_bot(token)
    operations: dict[str, bool] = {}
    errors: dict[str, str] = {}
    try:
        message = await _attempt(
            operations,
            errors,
            "sendMessage",
            lambda: bot.send_message(chat_id=chat_id, text="Arelbot staging mutation probe"),
        )
        if message is not None:
            await _attempt(
                operations,
                errors,
                "editMessageText",
                lambda: bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(message.message_id),
                    text="Arelbot staging mutation probe tamamlandı",
                ),
            )
        else:
            operations["editMessageText"] = False
            errors["editMessageText"] = "send_message_failed"

        # A normal supergroup has no forum topic.  In that case there is no
        # valid ``message_thread_id`` to probe, so omit the forum operation
        # rather than manufacturing a failure for an unrelated transport
        # surface.  Forum staging still exercises the operation whenever the
        # operator supplies an explicit topic id.
        if thread_id is not None:
            await _attempt(
                operations,
                errors,
                "sendMessageForum",
                lambda: bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text="Arelbot forum staging probe",
                ),
            )

        if video_file_id:
            await _attempt(
                operations,
                errors,
                "sendVideo",
                lambda: bot.send_video(chat_id=chat_id, video=video_file_id, caption="Arelbot staging video"),
            )
        else:
            operations["sendVideo"] = False
            errors["sendVideo"] = "video_file_id_required"

        if file_id:
            await _attempt(
                operations,
                errors,
                "getFile",
                lambda: bot.get_file(file_id),
            )
        else:
            operations["getFile"] = False
            errors["getFile"] = "file_id_required"

        if file_id:
            await _attempt(
                operations,
                errors,
                "sendDocument",
                lambda: bot.send_document(chat_id=chat_id, document=file_id, caption="Arelbot staging document"),
            )
        else:
            operations["sendDocument"] = False
            errors["sendDocument"] = "file_id_required"

        if sticker_file_id:
            await _attempt(
                operations,
                errors,
                "sendSticker",
                lambda: bot.send_sticker(chat_id=chat_id, sticker=sticker_file_id),
            )
        else:
            operations["sendSticker"] = False
            errors["sendSticker"] = "sticker_file_id_required"

        if callback_query_id:
            await _attempt(
                operations,
                errors,
                "answerCallbackQuery",
                lambda: bot.answer_callback_query(callback_query_id, text="Arelbot staging callback"),
            )
        else:
            operations["answerCallbackQuery"] = False
            errors["answerCallbackQuery"] = "callback_query_id_required"
    finally:
        await bot.shutdown()

    passed = sum(1 for value in operations.values() if value)
    failed = len(operations) - passed
    return {
        "scope": "telegram-mutation-live",
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "operations": operations,
        "errors": errors,
        "secret_free": True,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--chat-id")
    parser.add_argument("--thread-id")
    parser.add_argument("--video-file-id")
    parser.add_argument("--sticker-file-id")
    parser.add_argument("--file-id", help="Telegram file id used for getFile and sendDocument")
    parser.add_argument("--callback-query-id")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--allow-mutation", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        values = read_dotenv(args.env_file.resolve())
        token = str(values.get("TELEGRAM_TOKEN", "")).strip()
        if not _TOKEN_RE.fullmatch(token):
            raise MutationProbeError("telegram_token_invalid")
        if not (args.live or args.confirm_live or args.allow_mutation):
            payload = {
                "scope": "telegram-mutation-validation",
                "ok": True,
                "mode": "validation-only",
                "checks": ["token_shape", "mutation_gate_not_entered"],
                "secret_free": True,
            }
            if args.report:
                _write_report(args.report, payload)
            print(json.dumps(payload, ensure_ascii=False) if args.as_json else "telegram_mutation=validation-only")
            return 0
        validate_live_gate(
            live=args.live,
            confirm_live=args.confirm_live,
            allow_mutation=args.allow_mutation,
            env_value=os.getenv("ARELBOT_TELEGRAM_MUTATION_LIVE"),
        )
        chat_id = parse_integer(args.chat_id or values.get("TELEGRAM_STAGING_CHAT_ID"), required=True, label="chat_id")
        thread_id = parse_integer(args.thread_id or values.get("TELEGRAM_STAGING_THREAD_ID"), required=False, label="thread_id")
        payload = asyncio.run(
            run_live(
                token,
                chat_id=int(chat_id),
                thread_id=thread_id,
                video_file_id=args.video_file_id or values.get("TELEGRAM_STAGING_VIDEO_FILE_ID"),
                sticker_file_id=args.sticker_file_id or values.get("TELEGRAM_STAGING_STICKER_FILE_ID"),
                file_id=args.file_id or values.get("TELEGRAM_STAGING_FILE_ID"),
                callback_query_id=args.callback_query_id or values.get("TELEGRAM_STAGING_CALLBACK_QUERY_ID"),
            )
        )
    except MutationProbeError as exc:
        payload = {"scope": "telegram-mutation-live", "ok": False, "error": str(exc), "secret_free": True}
    except Exception:
        payload = {"scope": "telegram-mutation-live", "ok": False, "error": "mutation_probe_failed", "secret_free": True}
    if args.report:
        try:
            _write_report(args.report, payload)
        except MutationProbeError:
            payload = {"scope": "telegram-mutation-live", "ok": False, "error": "report_write_failed", "secret_free": True}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"telegram_mutation={'ok' if payload.get('ok') else 'failed'}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
