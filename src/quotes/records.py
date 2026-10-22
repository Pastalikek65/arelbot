"""Bounded durable records for generated quotes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .options import normalize_quote_options


def build_quote_record(
    *,
    chat_id: int,
    messages: Sequence[Mapping[str, Any]],
    options: Mapping[str, Any] | None,
    sticker_file_id: str | None,
) -> dict[str, Any]:
    """Create a replayable quote record without retaining unbounded payloads."""

    normalized_options = normalize_quote_options(options)
    bounded_messages: list[dict[str, Any]] = []
    for source in list(messages or [])[:10]:
        try:
            user_id = int(source.get("user_id") or 0)
        except (TypeError, ValueError):
            user_id = 0
        item = {
            "text": str(source.get("text") or "[Medya]")[:4000],
            "name": str(source.get("name") or "Anonim")[:128],
            "user_id": user_id,
        }
        if source.get("message_id") is not None:
            item["message_id"] = str(source["message_id"])[:128]
        bounded_messages.append(item)
    if not bounded_messages:
        raise ValueError("quote_messages_required")

    first = bounded_messages[0]
    return {
        "chat_id": int(chat_id),
        "user_id": int(first["user_id"]),
        "message_id": first.get("message_id", 0),
        "content": first["text"],
        "author_name": first["name"],
        "bg_color": normalized_options.get("bg_color"),
        "sticker_file_id": str(sticker_file_id)[:512] if sticker_file_id else None,
        "message_count": len(bounded_messages),
        "source_messages": bounded_messages,
        "render_options": normalized_options,
        "output_type": "sticker" if sticker_file_id else (
            "png" if normalized_options["is_png"] else "webp"
        ),
    }

