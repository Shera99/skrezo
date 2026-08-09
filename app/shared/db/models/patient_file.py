"""Таблица файлов пациента: снимки и документы.

Сам файл лежит на диске (``app/shared/storage/files.py``), здесь — метаданные и
принадлежность. В БД файл не кладём: снимки весят мегабайты, и таблица с ними
превращает бэкап и репликацию в отдельную проблему.

Файл привязан к **пациенту**, а пациент глобальный: снимок, загруженный одной
клиникой, виден всем клиникам, которые лечат этого человека, — как и сквозная
история зуба. Это осознанно: смысл платформы в том, что врач видит, что делали
до него. ``clinic_id`` при этом хранится — по нему видно, кто загрузил.

Удаление **мягкое**: строка помечается ``deleted_at``, файл на диске остаётся.
Снимок — клинические данные, и «случайно удалил» не должно означать «потеряли
навсегда». Физическую уборку по помеченным записям делает отдельная задача.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import FileKind, FileKindType


class PatientFile(Base, UUIDPKMixin):
    __tablename__ = "patient_file"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    #: Клиника, которая загрузила файл (не «владелец» — доступ идёт по пациенту).
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    #: Приём, к которому относится файл; ``None`` — просто в карту пациента.
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[FileKind] = mapped_column(FileKindType, nullable=False)
    #: Имя, под которым файл прислали, — только для показа и скачивания.
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Путь внутри хранилища (``images/2026/08/10/<uuid>.jpg``), имя генерируем мы.
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
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
        Index("ix_patient_file_patient_created", "patient_id", created_at.desc()),
    )
