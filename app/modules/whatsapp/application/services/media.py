"""Сохранение медиа WhatsApp на диск.

Отдельно от ``shared/storage/files.py``: тот заточен под загрузку через
``UploadFile`` с проверкой сигнатур, а здесь байты уже скачаны с серверов Meta
по авторизованной ссылке, и MIME сообщает сама Meta. Правила совпадают в
главном: имя на диске генерируем сами, раздачи статикой нет — только ручка с
проверкой доступа.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio
import mutagen

from app.shared.storage.files import storage_root

#: MIME → расширение; всё незнакомое хранится как .bin (тип остаётся в БД).
_EXT: dict[str, str] = {
    "audio/ogg": "ogg",  # голосовые WhatsApp
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/amr": "amr",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "application/pdf": "pdf",
}


async def save_media(content: bytes, mime: str) -> str:
    """Сохранить медиа, вернуть относительный путь внутри хранилища."""
    ext = _EXT.get(mime.split(";")[0].strip(), "bin")
    now = datetime.now(UTC)
    relative = Path("whatsapp") / f"{now:%Y/%m/%d}" / f"{uuid.uuid4()}.{ext}"
    target = storage_root() / relative
    await anyio.to_thread.run_sync(lambda: target.parent.mkdir(parents=True, exist_ok=True))
    await anyio.to_thread.run_sync(target.write_bytes, content)
    return str(relative)


def media_duration_seconds(content: bytes) -> int | None:
    """Длительность аудио в секундах по содержимому; ``None`` — не разобрали.

    Meta в вебхуке длительность голосового не присылает, поэтому читаем её из
    самого файла (Ogg Opus у WhatsApp). Неразобранный файл — не ошибка: превью
    покажется без времени, само вложение при этом сохранено и открывается.
    """
    try:
        parsed = mutagen.File(io.BytesIO(content))
    except Exception:  # mutagen кидает разное на мусорных данных — нам важен итог
        return None
    info = getattr(parsed, "info", None) if parsed is not None else None
    length = getattr(info, "length", None)
    if not length:
        return None
    return max(1, round(float(length)))
