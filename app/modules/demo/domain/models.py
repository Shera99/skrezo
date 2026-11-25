"""Доменные структуры демо-клиники."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DemoState:
    """Признак демо у клиники и граница, с которой её данные удаляет сброс."""

    clinic_id: uuid.UUID
    demo: bool
    since: datetime | None
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class DueClinic:
    """Демо-клиника глазами задания по расписанию."""

    clinic_id: uuid.UUID
    timezone: str | None
    reset_at: datetime | None


@dataclass(slots=True)
class Purged:
    """Сколько удалил сброс и какие файлы теперь можно убрать с диска."""

    patients: int = 0
    appointments: int = 0
    payments: int = 0
    conversations: int = 0
    file_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResetResult:
    """Итог сброса: что удалено и что собрано."""

    reset_at: datetime
    purged: Purged
    patient_ids: list[uuid.UUID]
    appointment_ids: list[uuid.UUID]
