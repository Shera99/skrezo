"""Юнит-тесты курсорной пагинации (без БД)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import BadRequestError
from app.shared.api.pagination import decode_cursor, encode_cursor


def test_cursor_roundtrip() -> None:
    ts = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    item_id = uuid.uuid4()
    decoded_ts, decoded_id = decode_cursor(encode_cursor(ts, item_id))
    assert decoded_ts == ts
    assert decoded_id == item_id


def test_bad_cursor_raises() -> None:
    with pytest.raises(BadRequestError) as exc:
        decode_cursor("не-валидный-курсор!!!")
    assert exc.value.code == "invalid_cursor"
