"""Курсорная (keyset) пагинация: непрозрачный курсор по (created_at, id).

Сортировка — по убыванию ``created_at``, затем ``id``. Курсор кодирует
последний элемент страницы; следующая страница берёт записи «строго меньше».
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime

from app.core.errors import BadRequestError


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    payload = json.dumps({"ts": created_at.isoformat(), "id": str(item_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        return datetime.fromisoformat(payload["ts"]), uuid.UUID(payload["id"])
    except (ValueError, KeyError, binascii.Error) as exc:
        raise BadRequestError("Некорректный курсор", code="invalid_cursor") from exc
