"""Хранилище загруженных файлов на диске сервера.

Раскладка — ``storage/<вид>/ГГГГ/ММ/ДД/<uuid>.<ext>``: по виду, чтобы снимки не
мешались с документами, и по дате, чтобы в одном каталоге не скапливались сотни
тысяч файлов (у ext4 это замедляет уже само открытие). Дата — UTC: каталоги
читает администратор сервера, и «сутки клиники» тут только запутали бы.

Три правила, каждое закрывает конкретный способ навредить:

* **имя на диске генерируем сами** (uuid + расширение из списка). Пользовательское
  имя хранится отдельно, в БД: иначе ``../../`` в имени уводит запись за пределы
  хранилища, а спецсимволы и совпадения ломают файлы друг друга;
* **тип проверяем по содержимому**, а не по заголовку запроса. Клиент называет
  тип сам, и `image/jpeg` с исполняемым файлом внутри — это ровно то, что потом
  скачает сотрудник;
* **размер считаем по мере чтения**, а не по ``Content-Length``: заголовок можно
  соврать, и «лимит» пропустит гигабайт на диск.

Раздачей статикой файлы не отдаются: только через ручку, которая проверяет
доступ клиники к пациенту. Иначе угаданный путь = чужой снимок.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio
from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import ValidationAppError
from app.shared.db.enums import FileKind

#: Размер куска при чтении загрузки (не держим файл в памяти целиком).
_CHUNK = 64 * 1024

#: Сигнатуры разрешённых типов: (magic-байты, смещение) → (mime, расширение, вид).
#: Список намеренно короткий — то, что реально нужно стоматологии. Расширять
#: следует сигнатурами, а не доверием к заголовку клиента.
_SIGNATURES: list[tuple[bytes, int, str, str, FileKind]] = [
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg", FileKind.IMAGE),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png", FileKind.IMAGE),
    (b"WEBP", 8, "image/webp", "webp", FileKind.IMAGE),
    (b"ftypheic", 4, "image/heic", "heic", FileKind.IMAGE),
    (b"ftypheix", 4, "image/heic", "heic", FileKind.IMAGE),
    (b"ftypmif1", 4, "image/heic", "heic", FileKind.IMAGE),
    (b"%PDF-", 0, "application/pdf", "pdf", FileKind.DOCUMENT),
]

#: Сколько байт достаточно, чтобы опознать тип.
_HEADER_BYTES = 32


class StoredFile:
    """Сохранённый файл: относительный путь и то, чем он оказался."""

    __slots__ = ("kind", "mime", "relative_path", "size_bytes")

    def __init__(self, relative_path: str, mime: str, kind: FileKind, size_bytes: int) -> None:
        self.relative_path = relative_path
        self.mime = mime
        self.kind = kind
        self.size_bytes = size_bytes


def storage_root() -> Path:
    """Корень хранилища (``STORAGE_ROOT``), созданный при необходимости."""
    root = Path(settings.storage_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def detect_type(header: bytes) -> tuple[str, str, FileKind]:
    """Опознать тип по началу файла; неизвестный — 422.

    Проверка идёт по содержимому: заголовок ``Content-Type`` присылает клиент, и
    доверять ему — значит принимать что угодно под видом снимка.
    """
    for magic, offset, mime, extension, kind in _SIGNATURES:
        if header[offset : offset + len(magic)] == magic:
            return mime, extension, kind
    raise ValidationAppError(
        "Такой тип файла не поддерживается: только JPEG, PNG, WebP, HEIC и PDF",
        code="unsupported_file_type",
    )


def resolve_stored_path(relative_path: str) -> Path:
    """Абсолютный путь к файлу, с проверкой, что он внутри хранилища.

    Путь генерируем мы сами, но проверка стоит копейки: испорченная строка в БД
    (миграция, ручная правка) иначе превращается в чтение произвольного файла с
    сервера через ручку скачивания.
    """
    root = storage_root().resolve()
    target = (root / relative_path).resolve()
    if not target.is_relative_to(root):
        raise ValidationAppError("Некорректный путь файла", code="invalid_file_path")
    return target


async def save_upload(upload: UploadFile) -> StoredFile:
    """Сохранить загрузку на диск; вернуть путь, тип и размер.

    Тип определяется по первому куску, размер — по мере чтения. Файл, не
    прошедший проверку, на диск не попадает вовсе; оборвавшаяся посередине
    загрузка — удаляется.
    """
    header = await upload.read(_HEADER_BYTES)
    if not header:
        raise ValidationAppError("Файл пустой", code="empty_file")
    mime, extension, kind = detect_type(header)

    today = datetime.now(UTC)
    directory = (
        storage_root() / kind.value / f"{today:%Y}" / f"{today:%m}" / f"{today:%d}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"
    target = directory / name

    size = len(header)
    limit = settings.upload_max_bytes
    try:
        with target.open("wb") as handle:
            await anyio.to_thread.run_sync(handle.write, header)
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise ValidationAppError(
                        f"Файл больше {limit // (1024 * 1024)} МБ", code="file_too_large"
                    )
                await anyio.to_thread.run_sync(handle.write, chunk)
    except Exception:
        # Недописанный файл не должен остаться в хранилище: он никому не
        # принадлежит (записи в БД ещё нет) и не будет удалён никогда.
        target.unlink(missing_ok=True)
        raise

    return StoredFile(
        relative_path=str(target.relative_to(storage_root())),
        mime=mime,
        kind=kind,
        size_bytes=size,
    )
