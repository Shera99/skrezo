"""Схемы модуля приёмов."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.appointments.domain.models import (
    AppointmentDetail,
    AppointmentRow,
    AppointmentToothRow,
)
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import AppointmentStatus, AppointmentToothStatus, AppointmentType


class AppointmentListItem(CamelModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_name: str | None = None
    appointment_type: AppointmentType
    scheduled_at: datetime
    status: AppointmentStatus
    price_total: int

    @classmethod
    def from_row(cls, r: AppointmentRow) -> AppointmentListItem:
        return cls(
            id=r.id,
            patient_id=r.patient_id,
            patient_name=r.patient_name,
            doctor_name=r.doctor_name,
            appointment_type=r.appointment_type,
            scheduled_at=r.scheduled_at,
            status=r.status,
            price_total=r.price_total,
        )


class AppointmentsResponse(CamelModel):
    list: list[AppointmentListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class ToothInputSchema(CamelModel):
    tooth: int = Field(description="Номер зуба FDI")
    service_id: uuid.UUID | None = None
    price: int | None = Field(default=None, ge=0, description="Цена; по умолч. из услуги")
    comment: str | None = None


class CreateAppointmentRequest(CamelModel):
    patient_id: uuid.UUID
    doctor_membership_id: uuid.UUID | None = None
    appointment_type: AppointmentType = AppointmentType.TREATMENT
    scheduled_at: datetime | None = None
    comment: str | None = None
    teeth: list[ToothInputSchema] = Field(default_factory=list, description="Манипуляции по зубам")


class AppointmentCreatedResponse(CamelModel):
    id: uuid.UUID
    price_total: int


class AppointmentToothOut(CamelModel):
    id: uuid.UUID
    tooth: int
    service_name: str | None = None
    status: AppointmentToothStatus
    price: int
    comment: str | None = None

    @classmethod
    def from_row(cls, r: AppointmentToothRow) -> AppointmentToothOut:
        return cls(
            id=r.id,
            tooth=r.tooth_number,
            service_name=r.service_name,
            status=r.status,
            price=r.price,
            comment=r.comment,
        )


class AppointmentDetailResponse(CamelModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_name: str | None = None
    appointment_type: AppointmentType
    scheduled_at: datetime
    status: AppointmentStatus
    price_total: int
    comment: str | None = None
    teeth: list[AppointmentToothOut]

    @classmethod
    def from_detail(cls, d: AppointmentDetail) -> AppointmentDetailResponse:
        a = d.appointment
        return cls(
            id=a.id,
            patient_id=a.patient_id,
            patient_name=d.patient_name,
            doctor_name=d.doctor_name,
            appointment_type=a.appointment_type,
            scheduled_at=a.scheduled_at,
            status=a.status,
            price_total=a.price_total,
            comment=a.comment,
            teeth=[AppointmentToothOut.from_row(t) for t in d.teeth],
        )


class UpdateAppointmentRequest(CamelModel):
    status: AppointmentStatus | None = None
    appointment_type: AppointmentType | None = None
    scheduled_at: datetime | None = None
    comment: str | None = None
    doctor_membership_id: uuid.UUID | None = None
