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

**КТ** приходит одиночным DICOM-файлом или ZIP-архивом исследования (сотни
срезов). Лимит у КТ свой (``UPLOAD_CT_MAX_BYTES``): исследование весит сотни
мегабайт, а поднимать общий лимит ради него значит пускать такие же объёмы
под видом фотографий. Архив не распаковывается: проверяются концевая запись,
оглавление и начало записей — внутри должен быть DICOM и не должно быть
программ. Архив открывает сотрудник другой клиники, и исполняемый файл в нём —
готовый способ занести заразу из клиники в клинику. По той же причине файл,
который начинается как программа, не принимается ни под каким видом, а
скачивается любой файл с расширением по настоящему типу.
"""

from __future__ import annotations

import os
import re
import uuid
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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
    # DICOM: 128 байт преамбулы (обычно нули) и «DICM». Стоит первым: преамбула
    # произвольна и в теории может начинаться как любой другой формат.
    (b"DICM", 128, "application/dicom", "dcm", FileKind.CT),
    (b"PK\x03\x04", 0, "application/zip", "zip", FileKind.CT),
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg", FileKind.IMAGE),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png", FileKind.IMAGE),
    (b"WEBP", 8, "image/webp", "webp", FileKind.IMAGE),
    (b"ftypheic", 4, "image/heic", "heic", FileKind.IMAGE),
    (b"ftypheix", 4, "image/heic", "heic", FileKind.IMAGE),
    (b"ftypmif1", 4, "image/heic", "heic", FileKind.IMAGE),
    (b"%PDF-", 0, "application/pdf", "pdf", FileKind.DOCUMENT),
]

#: Сколько байт достаточно, чтобы опознать тип: метка DICOM стоит на 128-м.
_HEADER_BYTES = 132

#: Сколько записей архива КТ просматривать в поисках DICOM. Обычно он первый
#: же (DICOMDIR или срез), а читать тысячи срезов ради проверки незачем.
_ZIP_SCAN_ENTRIES = 200

#: Потолок числа записей в архиве КТ: исследование — сотни, редко тысячи срезов.
_ZIP_MAX_ENTRIES = 20_000

#: Сжатие, которое ``zipfile`` разжимает с ограничением объёма.
_ZIP_SAFE_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

#: Концевые записи ZIP (EOCD) и ZIP64: подписи и размеры.
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_EOCD_MAX_COMMENT = 0xFFFF
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_EOCD_SIZE = 56

#: Расширения программ, скриптов, ярлыков, образов дисков и вложенных архивов,
#: с которыми архив КТ не принимается. Вложенный архив и образ — это способ
#: пронести то же самое мимо проверки имён.
_EXECUTABLE_SUFFIXES = frozenset(
    {
        ".exe", ".com", ".bat", ".cmd", ".scr", ".pif", ".msi", ".msp", ".msix",
        ".appx", ".dll", ".sys", ".cpl", ".ocx", ".js", ".jse", ".vbs", ".vbe",
        ".wsf", ".wsh", ".ps1", ".psm1", ".hta", ".lnk", ".url", ".scf", ".reg",
        ".inf", ".jar", ".app", ".sh", ".command", ".apk", ".dmg", ".pkg",
        ".zip", ".7z", ".rar", ".iso", ".img", ".vhd", ".vhdx",
    }
)  # fmt: skip

#: Начало исполняемых файлов (PE, ELF, Mach-O). Сигнатуры DICOM, WebP и HEIC
#: стоят не в начале файла, и их можно приклеить к программе: такой «снимок»
#: (CVE-2019-11687 для DICOM) скачает и запустит сотрудник другой клиники.
_EXECUTABLE_MAGIC = (
    b"MZ",
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)


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
    if header.startswith(_EXECUTABLE_MAGIC):
        raise ValidationAppError(
            "Это программа, а не снимок или документ — такие файлы не принимаются",
            code="unsupported_file_type",
        )
    for magic, offset, mime, extension, kind in _SIGNATURES:
        if header[offset : offset + len(magic)] == magic:
            return mime, extension, kind
    raise ValidationAppError(
        "Такой тип файла не поддерживается: только JPEG, PNG, WebP, HEIC, PDF "
        "и КТ (DICOM или ZIP-архив с DICOM)",
        code="unsupported_file_type",
    )


def upload_limit(kind: FileKind) -> int:
    """Потолок размера для вида файла: у КТ он свой, у остальных — общий."""
    if kind is FileKind.CT:
        return settings.upload_ct_max_bytes
    return settings.upload_max_bytes


def check_ct_archive(path: Path) -> None:
    """Проверить ZIP-архив КТ, не распаковывая его; не годится — 422.

    Порядок проверок выбран так, чтобы ни одна не стоила памяти и времени:

    1. число записей — по концевой записи архива, ДО ``ZipFile()``: оглавление
       тот строит в памяти целиком, и архив из миллионов пустых записей
       съедает гигабайты ещё до первой проверки;
    2. имена — ни одной программы или скрипта, в том числе внутри пути
       (``Viewer.app/…``) и с хвостом, который Windows срежет при распаковке;
    3. сжатие — только stored/deflate: у bzip2/lzma/zstd ``zipfile`` разжимает
       без предела, и 4 КБ превращаются в гигабайты (zip-бомба);
    4. содержимое — среди первых записей есть DICOM; читается по 132 байта.
    """
    if zip_entry_count(path) > _ZIP_MAX_ENTRIES:
        raise ValidationAppError(
            f"В архиве больше {_ZIP_MAX_ENTRIES} файлов — это не похоже на одно исследование",
            code="archive_too_many_entries",
        )
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            executable = next((i.filename for i in entries if _is_executable(i.filename)), None)
            if executable is not None:
                raise ValidationAppError(
                    "В архиве есть программа или скрипт — загрузите только снимки "
                    "исследования (без программы просмотра)",
                    code="archive_has_executable",
                    extra={"entry": executable},
                )
            if any(i.compress_type not in _ZIP_SAFE_COMPRESSION for i in entries):
                raise ValidationAppError(
                    "Архив сжат неподдерживаемым способом — пересоберите его как обычный ZIP",
                    code="archive_unsupported_compression",
                )
            for info in entries[:_ZIP_SCAN_ENTRIES]:
                if info.flag_bits & 0x1:
                    raise ValidationAppError(
                        "Архив защищён паролем — загрузите его без пароля",
                        code="archive_encrypted",
                    )
                with archive.open(info) as entry:
                    if entry.read(_HEADER_BYTES)[128:132] == b"DICM":
                        return
    except zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, OSError:
        raise ValidationAppError("Архив повреждён", code="invalid_archive") from None
    raise ValidationAppError(
        "В архиве нет снимков DICOM — для КТ нужен экспорт исследования в DICOM",
        code="archive_without_dicom",
    )


def zip_entry_count(path: Path) -> int:
    """Число записей архива по его концевой записи (EOCD), без разбора оглавления.

    Концевая запись — последние 22 байта плюс комментарий до 64 КБ; число
    записей в ней — 2 байта на смещении 10. ``0xFFFF`` значит ZIP64: настоящее
    число — в концевой записи ZIP64, адрес которой хранит локатор прямо перед
    обычной. Нет записи или локатора — архив повреждён (422).
    """
    broken = ValidationAppError("Архив повреждён", code="invalid_archive")
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        tail_size = min(size, _EOCD_SIZE + _EOCD_MAX_COMMENT)
        handle.seek(size - tail_size)
        tail = handle.read(tail_size)
        pos = tail.rfind(_EOCD_SIGNATURE)
        if pos < 0 or len(tail) < pos + _EOCD_SIZE:
            raise broken
        count = int.from_bytes(tail[pos + 10 : pos + 12], "little")
        if count != 0xFFFF:
            return count
        locator = tail[pos - _ZIP64_LOCATOR_SIZE : pos] if pos >= _ZIP64_LOCATOR_SIZE else b""
        if not locator.startswith(_ZIP64_LOCATOR_SIGNATURE):
            raise broken
        handle.seek(int.from_bytes(locator[8:16], "little"))
        record = handle.read(_ZIP64_EOCD_SIZE)
        if not record.startswith(_ZIP64_EOCD_SIGNATURE) or len(record) < _ZIP64_EOCD_SIZE:
            raise broken
        return int.from_bytes(record[32:40], "little")


def _is_executable(name: str) -> bool:
    """Программа или скрипт — по любому компоненту пути, как его увидит Windows.

    Каталоги приложений macOS (``Viewer.app/Contents/…``) выдают себя именем
    каталога, а Windows при распаковке срезает хвостовые точки и пробелы и
    отбрасывает поток после двоеточия: ``viewer.exe.`` и ``viewer.exe::$DATA``
    станут ``viewer.exe``.
    """
    for part in re.split(r"[\\/]", name):
        clean = part.split(":", 1)[0].rstrip(". ")
        if PurePosixPath(clean).suffix.lower() in _EXECUTABLE_SUFFIXES:
            return True
    return False


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


def download_name(original_name: str, stored_path: str) -> str:
    """Имя для скачивания: присланное, но с расширением по НАСТОЯЩЕМУ типу.

    Тип файла мы определили по содержимому, а имя прислал клиент. Отдавать
    ``viewer.exe`` с содержимым, которое прошло как DICOM, значит дать
    сотруднику другой клиники запускаемый файл одним двойным щелчком.
    """
    extension = PurePosixPath(stored_path).suffix
    name = re.split(r"[\\/]", original_name)[-1].strip() or "file"
    if name.lower().endswith(extension.lower()):
        return name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{stem.rstrip('. ') or 'file'}{extension}"


async def save_upload(upload: UploadFile) -> StoredFile:
    """Сохранить загрузку на диск; вернуть путь, тип и размер.

    Тип определяется по первому куску, размер — по мере чтения (лимит зависит
    от вида: у КТ он свой). Файл, не прошедший проверку, на диск не попадает
    вовсе; оборвавшаяся посередине загрузка и архив, не прошедший проверку
    содержимого, — удаляются.
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
    limit = upload_limit(kind)
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
        if mime == "application/zip":
            await anyio.to_thread.run_sync(check_ct_archive, target)
    except BaseException:
        # Недописанный файл не должен остаться в хранилище: он никому не
        # принадлежит (записи в БД ещё нет) и не будет удалён никогда.
        # BaseException — ради отмены: при перезапуске uvicorn отменяет идущие
        # запросы, и копирование гигабайта КТ обрывается CancelledError.
        target.unlink(missing_ok=True)
        raise

    return StoredFile(
        relative_path=str(target.relative_to(storage_root())),
        mime=mime,
        kind=kind,
        size_bytes=size,
    )
