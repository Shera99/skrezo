"""Таблица файлов пациента: снимки, документы и КТ.

Сам файл лежит на диске (``app/shared/storage/files.py``), здесь — метаданные и
принадлежность. В БД файл не кладём: снимки весят мегабайты, и таблица с ними
превращает бэкап и репликацию в отдельную проблему.

Файл привязан к **пациенту**, а пациент глобальный: снимок, загруженный одной
клиникой, виден всем клиникам, которые лечат этого человека, — как и сквозная
история зуба. Это осознанно: смысл платформы в том, что врач видит, что делали
до него. ``clinic_id`` при этом хранится — по нему видно, кто загрузил, и только
эта клиника вправе править описание файла и удалять его.

Файл может относиться к зубам (``tooth_numbers``) — тогда он показывается у
этих зубов в формуле; пустой список — общий файл пациента (например, КТ всей
челюсти).

Удаление **мягкое**: строка помечается ``deleted_at``, файл на диске остаётся.
Снимок — клинические данные, и «случайно удалил» не должно означать «потеряли
навсегда». Физическую уборку по помеченным записям делает отдельная задача.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, SmallInteger, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import FileKind, FileKindType


class PatientFile(Base, UUIDPKMixin):
    __tablename__ = "patient_file"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    #: Клиника, которая загрузила файл: видят его все клиники пациента, а
    #: править и удалять может только она.
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    #: Приём, к которому относится файл; ``None`` — просто в карту пациента.
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[FileKind] = mapped_column(FileKindType, nullable=False)
    #: Зубы (FDI), к которым относится файл; пустой список — общий файл пациента.
    tooth_numbers: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default=text("'{}'")
    )
    #: Дата самого снимка, если известна (пациент принёс КТ годичной давности);
    #: ``None`` — считаем датой загрузки.
    taken_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Имя, под которым файл прислали, — только для показа и скачивания.
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Путь внутри хранилища (``images/2026/08/10/<uuid>.jpg``), имя генерируем мы.
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    #: BIGINT: исследование КТ весит сотни мегабайт, а лимит задаётся настройкой.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Карта пациента открывается постоянно и всегда просит свежие сверху.
        # Отбор по зубу индекса не требует: файлов у одного пациента — десятки.
        Index("ix_patient_file_patient_created", "patient_id", created_at.desc()),
    )
