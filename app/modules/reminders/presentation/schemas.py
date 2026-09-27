"""Схемы модуля напоминаний (camelCase наружу)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.reminders.domain.models import (
    ReminderKind,
    ReminderMarkRecord,
    ReminderMarksPage,
)
from app.shared.api.schemas import CamelModel


class MarkRemindedRequest(CamelModel):
    """Отметка «напомнили»: кому, по какому поводу, что сказали."""

    patient_id: uuid.UUID
    kind: ReminderKind = Field(
        description=(
            "Повод: recall — повторный осмотр, debt — долг по приёму, visit — предстоящий "
            "приём, other — прочее"
        ),
        examples=["recall"],
    )
    appointment_id: uuid.UUID | None = Field(
        default=None,
        description="Приём-источник повода: долг по нему, визит или последний осмотр",
    )
    comment: str | None = Field(
        default=None,
        max_length=500,
        description="Что сказал пациент или как связались",
        examples=["Позвонили, обещала прийти в октябре"],
    )


class ReminderMarkOut(CamelModel):
    """Отметка в журнале — с именами пациента и сотрудника."""

    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str = Field(examples=["Асанова Айгүл"])
    kind: ReminderKind
    appointment_id: uuid.UUID | None = None
    comment: str | None = None
    marked_by_membership_id: uuid.UUID | None = Field(
        default=None, description="Кто отметил (членство); null — сотрудник удалён"
    )
    marked_by_name: str | None = Field(default=None, examples=["Иванова Айнура"])
    marked_at: datetime = Field(
        description="Когда отметили, в таймзоне клиники", examples=["2026-09-27T11:40:00+06:00"]
    )

    @classmethod
    def from_record(cls, r: ReminderMarkRecord) -> ReminderMarkOut:
        return cls(
            id=r.id,
            patient_id=r.patient_id,
            patient_name=r.patient_name,
            kind=r.kind,
            appointment_id=r.appointment_id,
            comment=r.comment,
            marked_by_membership_id=r.marked_by_membership_id,
            marked_by_name=r.marked_by_name,
            marked_at=r.marked_at,
        )


class ReminderMarksResponse(CamelModel):
    """Журнал отметок (offset-пагинация, последние сверху)."""

    list: list[ReminderMarkOut]
    total_count: int
    current_page: int
    page_size: int

    @classmethod
    def from_page(cls, page: ReminderMarksPage) -> ReminderMarksResponse:
        return cls(
            list=[ReminderMarkOut.from_record(r) for r in page.items],
            total_count=page.total_count,
            current_page=page.current_page,
            page_size=page.page_size,
        )


class ReminderMarkDeletedResponse(CamelModel):
    """Подтверждение удаления отметки."""

    id: uuid.UUID
