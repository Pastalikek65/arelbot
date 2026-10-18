from __future__ import annotations

import pytest

from src.quotes.records import build_quote_record


def test_quote_record_preserves_bounded_batch_and_render_options():
    record = build_quote_record(
        chat_id=-100,
        messages=[
            {"text": "Birinci", "name": "Ali", "user_id": 7, "message_id": 42},
            {"text": "İkinci", "name": "Ayşe", "user_id": 8, "message_id": 43},
        ],
        options={"theme": "telegram", "scale": 1.5, "is_png": True},
        sticker_file_id=None,
    )

    assert record["message_count"] == 2
    assert record["source_messages"][1]["message_id"] == "43"
    assert record["render_options"]["theme"] == "telegram"
    assert record["output_type"] == "png"


def test_quote_record_bounds_text_names_and_message_count():
    record = build_quote_record(
        chat_id=1,
        messages=[
            {"text": "x" * 10000, "name": "n" * 1000, "user_id": "bad"}
            for _ in range(15)
        ],
        options={},
        sticker_file_id="file-id",
    )

    assert record["message_count"] == 10
    assert len(record["source_messages"][0]["text"]) == 4000
    assert len(record["source_messages"][0]["name"]) == 128
    assert record["user_id"] == 0
    assert record["output_type"] == "sticker"


def test_quote_record_requires_at_least_one_source_message():
    with pytest.raises(ValueError, match="quote_messages_required"):
        build_quote_record(chat_id=1, messages=[], options={}, sticker_file_id=None)

