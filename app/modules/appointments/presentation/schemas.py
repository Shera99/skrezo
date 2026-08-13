"""Схемы модуля приёмов."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.modules.appointments.domain.models import (
    AppointmentDetail,
    AppointmentItemRow,
    AppointmentRow,
)
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import (
    AppointmentItemStatus,
    AppointmentStatus,
    AppointmentType,
    CancelReason,
    PaymentMethod,
)

# Верхняя граница длительности приёма — сутки. Без неё durationMin: 999999999
# проходит (у приёма без врача проверок занятости нет) и растягивает карточку
# на годы вперёд.
MAX_DURATION_MIN = 24 * 60
# Потолок количества в позиции: столько единиц одной услуги за приём не оказывают,
# а без границы опечатка в количестве раздувает чек и выручку в отчётах.
MAX_ITEM_QUANTITY = 999


class AppointmentListItem(CamelModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_name: str | None = None
    appointment_type: AppointmentType
    scheduled_at: datetime | None = Field(
        default=None, description="null у черновика (status=draft) — дата ещё не выбрана"
    )
    duration_min: int = Field(description="Длительность, минуты", examples=[45])
    status: AppointmentStatus
    price_total_tiyin: int = Field(description="Итог приёма в тыйынах", examples=[425000])

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
            price_total_tiyin=r.price_total_tiyin,
        )


class AppointmentsResponse(CamelModel):
    list: list[AppointmentListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class ItemInputSchema(CamelModel):
    """Позиция чека: услуга, цена за единицу, количество и зубы.

    Реальный приём многопозиционный («кариес 36 + чистка»), поэтому позиций
    может быть несколько. `teeth` пуст у работ, не привязанных к зубу (чистка,
    консультация), и содержит несколько номеров, если одна работа покрывает
    несколько зубов (мост на 35–37).
    """

    service_id: uuid.UUID | None = Field(
        default=None, description="Услуга из прайса клиники; без неё цену задают вручную"
    )
    price_tiyin: int | None = Field(
        default=None,
        ge=0,
        description="Цена ЗА ЕДИНИЦУ в тыйынах; по умолчанию — цена услуги на момент оказания",
        examples=[150000],
    )
    quantity: int = Field(
        default=1, ge=1, le=MAX_ITEM_QUANTITY, description="Количество", examples=[2]
    )
    teeth: list[int] = Field(
        default_factory=list,
        description="Номера зубов FDI; пусто — работа не привязана к зубу",
        examples=[[36, 37]],
    )
    status: AppointmentItemStatus = Field(
        default=AppointmentItemStatus.PLANNED,
        description=(
            "`planned` — запланировано, `started` — в работе, `completed` — сделано, "
            "`cancelled` — от позиции отказались. Завершение приёма само доводит "
            "`planned`/`started` до `completed`, отменённые не трогает"
        ),
        examples=["completed"],
    )
    comment: str | None = None


class ApplyDiscountRequest(CamelModel):
    """Скидка на приём процентом (0 — снять)."""

    percent: int = Field(
        ge=0,
        le=100,
        description=(
            "Размер скидки, %. Ограничен потолком роли (`maxDiscountPercent`); "
            "сверх него — 403. `0` снимает скидку"
        ),
        examples=[10],
    )


class ReplaceItemsRequest(CamelModel):
    """Новый список позиций приёма — заменяет прежний целиком."""

    items: list[ItemInputSchema] = Field(
        default_factory=list, description="Позиции чека; пустой список очищает чек"
    )
    force: bool = Field(
        default=False,
        description=(
            "Положить в чек услугу, которую врач приёма не выполняет "
            "(право `appointments.force_booking`). Без него — 422 "
            "`doctor_cant_do_service` со списком услуг в `serviceIds`"
        ),
    )


class CreateAppointmentRequest(CamelModel):
    patient_id: uuid.UUID
    doctor_membership_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Врач = членство в клинике. Обязателен для всех статусов, кроме "
            "`draft`: без врача приём не попадает ни в одну колонку ленты дня и "
            "не занимает слот. Пропуск → 422 `doctor_required`"
        ),
    )
    service_id: uuid.UUID | None = Field(
        default=None, description="Основная услуга приёма — источник длительности"
    )
    appointment_type: AppointmentType = AppointmentType.TREATMENT
    status: Literal[AppointmentStatus.SCHEDULED, AppointmentStatus.DRAFT] = Field(
        default=AppointmentStatus.SCHEDULED,
        description=(
            "`scheduled` — обычная запись, `scheduledAt` обязателен; `draft` — "
            "пациент заведён в клинику, но дата и услуга ещё не выбраны "
            "(`scheduledAt` передавать нельзя). Это единственный приём без даты. "
            "Остальные статусы выставляются через PATCH или «завершить и оплатить»"
        ),
        examples=["draft"],
    )
    scheduled_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "Начало приёма со СМЕЩЕНИЕМ таймзоны (2026-07-20T09:00:00+06:00). "
            "**Обязателен**, если `status` не `draft`: без даты → 422 "
            "`scheduled_at_required`. Дефолта «сейчас» нет — запись на текущий "
            "момент передаётся явным временем. Время без смещения отвергается: "
            "трактовать его пришлось бы по таймзоне сервера, и момент зависел бы "
            "от того, где запущено приложение"
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
    items: list[ItemInputSchema] = Field(
        default_factory=list,
        description=(
            "Позиции чека (услуга, цена за единицу, количество, зубы). Приём без "
            "позиций завести можно — но закрыть его потом получится только как "
            "бесплатный (`noCharge`)"
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "Записать вне рабочих часов врача (право `appointments.force_booking`). "
            "Снимает только проверку графика и обеда: пересечение с другим приёмом "
            "и отпуск врача не обходятся никогда"
        ),
        examples=[True],
    )

    @model_validator(mode="after")
    def _check_draft(self) -> CreateAppointmentRequest:
        """У черновика даты быть не может — иначе это обычная запись."""
        if self.status is AppointmentStatus.DRAFT and self.scheduled_at is not None:
            raise ValueError("У черновика не может быть даты приёма: уберите scheduledAt")
        return self


class AppointmentCreatedResponse(CamelModel):
    id: uuid.UUID
    price_total_tiyin: int = Field(description="Итог приёма в тыйынах", examples=[425000])


class AppointmentItemOut(CamelModel):
    """Позиция чека в карточке приёма."""

    id: uuid.UUID
    service_id: uuid.UUID | None = None
    service_name: str | None = None
    status: AppointmentItemStatus
    price_tiyin: int = Field(description="Цена за единицу в тыйынах", examples=[425000])
    quantity: int = Field(description="Количество", examples=[2])
    total_tiyin: int = Field(description="Итог строки: цена × количество", examples=[850000])
    teeth: list[int] = Field(
        description="Номера зубов FDI; пусто — работа не привязана к зубу", examples=[[36, 37]]
    )
    comment: str | None = None

    @classmethod
    def from_row(cls, r: AppointmentItemRow) -> AppointmentItemOut:
        return cls(
            id=r.id,
            service_id=r.service_id,
            service_name=r.service_name,
            status=r.status,
            price_tiyin=r.price_tiyin,
            quantity=r.quantity,
            total_tiyin=r.total_tiyin,
            teeth=r.teeth,
            comment=r.comment,
        )


class AppointmentDetailResponse(CamelModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_name: str | None = None
    appointment_type: AppointmentType
    scheduled_at: datetime | None = Field(
        default=None, description="null у черновика (status=draft) — дата ещё не выбрана"
    )
    duration_min: int = Field(description="Длительность, минуты", examples=[45])
    service_id: uuid.UUID | None = None
    status: AppointmentStatus
    price_total_tiyin: int = Field(
        description="Итог К ОПЛАТЕ в тыйынах: сумма позиций минус скидка", examples=[382500]
    )
    subtotal_tiyin: int = Field(
        description="Сумма позиций до скидки, тыйыны", examples=[425000]
    )
    discount_percent: int = Field(description="Скидка, %", examples=[10])
    discount_tiyin: int = Field(description="Сумма скидки в тыйынах", examples=[42500])
    comment: str | None = None
    cancel_reason: CancelReason | None = Field(
        default=None, description="Причина отмены; null у неотменённого приёма"
    )
    cancel_comment: str | None = Field(default=None, description="Пояснение к отмене")
    no_charge: bool = Field(
        default=False, description="Приём закрыт как бесплатный (без позиций)"
    )
    no_charge_comment: str | None = Field(
        default=None, description="Пояснение к бесплатному приёму"
    )
    items: list[AppointmentItemOut] = Field(description="Позиции чека")

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
            price_total_tiyin=a.price_total_tiyin,
            subtotal_tiyin=a.price_total_tiyin + a.discount_tiyin,
            discount_percent=a.discount_percent,
            discount_tiyin=a.discount_tiyin,
            comment=a.comment,
            cancel_reason=a.cancel_reason,
            cancel_comment=a.cancel_comment,
            no_charge=a.no_charge,
            no_charge_comment=a.no_charge_comment,
            items=[AppointmentItemOut.from_row(i) for i in d.items],
        )


class UpdateAppointmentRequest(NotNullFieldsModel):
    """Правка приёма: передаются только изменяемые поля.

    ``null`` осмыслен лишь для полей, которые в БД NULLABLE (снять врача, услугу,
    комментарий). Для NOT NULL-полей ``null`` — ошибка валидации (422): раньше он
    доезжал до слепого ``setattr`` и записывал NULL в NOT NULL-колонку.
    """

    NOT_NULL_FIELDS = ("status", "appointmentType", "scheduledAt", "durationMin", "noCharge")

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
    cancel_reason: CancelReason | None = Field(
        default=None,
        description=(
            "Причина отмены из справочника — обязательна при `status: cancelled` "
            "и запрещена без неё. При снятии отмены очищается автоматически"
        ),
        examples=["patient_illness"],
    )
    cancel_comment: str | None = Field(
        default=None,
        max_length=500,
        description="Пояснение к отмене; обязательно при `cancelReason: other`",
        examples=["Перенёс на следующую неделю"],
    )
    rollback_comment: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Обязателен при откате статуса назад по воронке (право "
            "`appointments.status_rollback`); попадает в аудит"
        ),
        examples=["Пациент опоздал, но пришёл"],
    )
    no_charge: bool | None = Field(
        default=None,
        description=(
            "Пометить приём бесплатным — единственный способ закрыть приём без "
            "позиций (`status: completed`). Требует `noChargeComment`; при "
            "`false` пояснение очищается"
        ),
        examples=[True],
    )
    no_charge_comment: str | None = Field(
        default=None,
        max_length=500,
        description="Пояснение к бесплатному приёму; обязательно при `noCharge: true`",
        examples=["Гарантийный осмотр после лечения"],
    )
    doctor_membership_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Сменить врача. Снять врача (`null`) можно только у черновика или "
            "отменённого приёма — иначе 422 `doctor_required`"
        ),
    )
    service_id: uuid.UUID | None = Field(
        default=None, description="Основная услуга приёма; null — снять услугу"
    )
    force: bool = Field(
        default=False,
        description=(
            "Перенести вне рабочих часов врача (право `appointments.force_booking`). "
            "Снимает только проверку графика и обеда: пересечение с другим приёмом "
            "и отпуск врача не обходятся никогда"
        ),
        examples=[True],
    )


class CompletePaymentInput(CamelModel):
    """Оплата, проводимая при завершении приёма (касса)."""

    amount_tiyin: int = Field(
        gt=0, description="Сумма платежа в тыйынах (> 0)", examples=[150000]
    )
    method: PaymentMethod = Field(description="Способ оплаты", examples=["cash"])
    comment: str | None = Field(default=None, max_length=500)


class CompleteAppointmentRequest(CamelModel):
    """Завершение приёма с опциональной оплатой.

    Без ``payment`` приём просто закрывается (остаток становится долгом). С
    ``payment`` оплата проводится в кассу в той же транзакции; переплата → 422.
    """

    payment: CompletePaymentInput | None = None
    no_charge: bool = Field(
        default=False,
        description=(
            "Закрыть приём как бесплатный — единственный способ закрыть приём "
            "без позиций. Требует `noChargeComment` и запрещён, если позиции есть"
        ),
        examples=[True],
    )
    no_charge_comment: str | None = Field(
        default=None,
        max_length=500,
        description="Пояснение к бесплатному приёму; обязательно при `noCharge: true`",
        examples=["Гарантийный осмотр после лечения"],
    )
