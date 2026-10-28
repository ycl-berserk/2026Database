from __future__ import annotations

from app.services.conversation_service import decode_cursor, encode_cursor


def test_cursor_round_trips_without_padding():
    payload = {
        "v": 1,
        "m": "lexical",
        "t": "2026-10-28T10:00:00+00:00",
        "i": 42,
        "n": 11,
    }
    cursor = encode_cursor(payload)
    assert "=" not in cursor
    assert decode_cursor(cursor) == payload


def test_invalid_cursor_is_ignored():
    for value in (None, "", "!!!", "e30", encode_cursor({"v": 2}), "not*base64"):
        assert decode_cursor(value) is None
