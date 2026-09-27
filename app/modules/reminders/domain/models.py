"""Доменные структуры отметок «напомнили».

Отметка — факт: сотрудник связался с пациентом по поводу. Она не отправляет
ничего сама (автоматические напоминания ассистента — модуль ``ai``), зато
видна всем администраторам клиники, а не только браузеру одного из них.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime


class ReminderKind(enum.StrEnum):
    """Повод напоминания — по нему экран «Напоминания» группирует отметки."""

    RECALL = "recall"  # повторный осмотр: давно не был
    DEBT = "debt"  # долг по завершённому приёму
    VISIT = "visit"  # предстоящий приём
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ReminderMarkRecord:
    """Отметка с именами пациента и сотрудника — как её показывает журнал."""

    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    kind: ReminderKind
    appointment_id: uuid.UUID | None
    comment: str | None
    marked_by_membership_id: uuid.UUID | None
    marked_by_name: str | None
    marked_at: datetime


@dataclass(frozen=True, slots=True)
class ReminderMarksPage:
    """Страница журнала отметок (offset-пагинация)."""

    items: list[ReminderMarkRecord]
    total_count: int
    current_page: int
    page_size: int
