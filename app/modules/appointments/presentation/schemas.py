"""Схемы модуля приёмов."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AwareDatetime, Field

from app.modules.appointments.domain.models import (
    AppointmentDetail,
    AppointmentRow,
    AppointmentToothRow,
)
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import (
    AppointmentStatus,
    AppointmentToothStatus,
    AppointmentType,
    PaymentMethod,
)

# Верхняя граница длительности приёма — сутки. Без неё durationMin: 999999999
# проходит (у приёма без врача проверок занятости нет) и растягивает карточку
# на годы вперёд.
MAX_DURATION_MIN = 24 * 60


class AppointmentListItem(CamelModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_name: str | None = None
    appointment_type: AppointmentType
    scheduled_at: datetime
    duration_min: int = Field(description="Длительность, минуты", examples=[45])
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
            duration_min=r.duration_min,
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
    service_id: uuid.UUID | None = Field(
        default=None, description="Основная услуга приёма — источник длительности"
    )
    appointment_type: AppointmentType = AppointmentType.TREATMENT
    scheduled_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "Начало приёма со СМЕЩЕНИЕМ таймзоны (2026-07-20T09:00:00+06:00). "
            "Время без смещения отвергается: трактовать его пришлось бы по таймзоне "
            "сервера, и момент зависел бы от того, где запущено приложение. "
            "По умолчанию — «сейчас»"
        ),
        examples=["2026-07-20T09:00:00+06:00"],
    )
    duration_min: int | None = Field(
        default=None,
        gt=0,
        le=MAX_DURATION_MIN,
        description=(
            f"Длительность, минуты (не больше {MAX_DURATION_MIN} — сутки). "
            "По умолчанию — из услуги, иначе 30"
        ),
        examples=[45],
    )
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
    duration_min: int = Field(description="Длительность, минуты", examples=[45])
    service_id: uuid.UUID | None = None
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
            duration_min=a.duration_min,
            service_id=a.service_id,
            status=a.status,
            price_total=a.price_total,
            comment=a.comment,
            teeth=[AppointmentToothOut.from_row(t) for t in d.teeth],
        )


class UpdateAppointmentRequest(NotNullFieldsModel):
    """Правка приёма: передаются только изменяемые поля.

    ``null`` осмыслен лишь для полей, которые в БД NULLABLE (снять врача, услугу,
    комментарий). Для NOT NULL-полей ``null`` — ошибка валидации (422): раньше он
    доезжал до слепого ``setattr`` и записывал NULL в NOT NULL-колонку.
    """

    NOT_NULL_FIELDS = ("status", "appointmentType", "scheduledAt", "durationMin")

    status: AppointmentStatus | None = None
    appointment_type: AppointmentType | None = None
    scheduled_at: AwareDatetime | None = Field(
        default=None,
        description="Перенос приёма; время обязано нести смещение таймзоны",
        examples=["2026-07-20T09:00:00+06:00"],
    )
    duration_min: int | None = Field(
        default=None,
        gt=0,
        le=MAX_DURATION_MIN,
        description=f"Длительность, минуты (не больше {MAX_DURATION_MIN} — сутки)",
        examples=[45],
    )
    comment: str | None = None
    doctor_membership_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = Field(
        default=None, description="Основная услуга приёма; null — снять услугу"
    )


class CompletePaymentInput(CamelModel):
    """Оплата, проводимая при завершении приёма (касса)."""

    amount: int = Field(gt=0, description="Сумма платежа в сомах (> 0)", examples=[1500])
    method: PaymentMethod = Field(description="Способ оплаты", examples=["cash"])
    comment: str | None = Field(default=None, max_length=500)


class CompleteAppointmentRequest(CamelModel):
    """Завершение приёма с опциональной оплатой.

    Без ``payment`` приём просто закрывается (остаток становится долгом). С
    ``payment`` оплата проводится в кассу в той же транзакции; переплата → 422.
    """

    payment: CompletePaymentInput | None = None
