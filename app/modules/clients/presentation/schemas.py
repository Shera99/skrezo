"""Схемы раздела «Клиенты» (контракты фронта админ-панели, 1:1).

Поля-заглушки (клиническое ядро/биллинг вне скоупа): `patientsCount`/`revenue`
= 0, `lifetimeRevenue`/`lastActiveAt`/`activity` = null, `payments` = [],
`cardAttached` = false (карты не привязываются). `tariffLabel` пока = `tariffName`
(пер-клиничный тариф не назначается).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import Field

from app.modules.clients.domain.models import ClientDetail, ClientListRow, StaffRow
from app.modules.clients.infrastructure.repositories.client_repository import tariff_name_for
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import ClinicType, Lang, SubscriptionStatus

_TRIAL_DAYS = 30


def _trial_day(status: SubscriptionStatus | None, trial_started_at: datetime | None) -> int | None:
    """Номер дня триала 1..30 (только для status=trial), иначе None."""
    if status is not SubscriptionStatus.TRIAL or trial_started_at is None:
        return None
    elapsed = (datetime.now(UTC) - trial_started_at).days + 1
    return max(1, min(_TRIAL_DAYS, elapsed))


class ClientListItem(CamelModel):
    id: uuid.UUID
    type: ClinicType
    name: str
    admin_full_name: str | None = None
    admin_phone: str | None = None
    city_name: str | None = None
    tariff_name: str = Field(description="Коарс-ярлык тарифа для фильтра: «Соло» | «Клиника»")
    tariff_label: str = Field(description="Человекочитаемый ярлык тарифа")
    seats_used: int
    seats_limit: int | None = None
    patients_count: int = 0
    revenue_tiyin: int = Field(default=0, description="Выручка кассы клиники (вне скоупа) — 0")
    lifetime_revenue_tiyin: int | None = None
    subscription_status: SubscriptionStatus | None = None
    trial_day: int | None = None
    last_active_at: datetime | None = None
    created_at: datetime
    current_period_end: datetime | None = None

    @classmethod
    def from_row(cls, row: ClientListRow) -> ClientListItem:
        name = tariff_name_for(row.type)
        return cls(
            id=row.id,
            type=row.type,
            name=row.name,
            admin_full_name=row.admin_full_name,
            admin_phone=row.admin_phone,
            city_name=row.city_name,
            tariff_name=name,
            tariff_label=name,
            seats_used=row.seats_used,
            seats_limit=row.seats_limit,
            subscription_status=row.subscription_status,
            trial_day=_trial_day(row.subscription_status, row.trial_started_at),
            created_at=row.created_at,
            current_period_end=row.current_period_end,
        )


class ClientsResponse(CamelModel):
    list: list[ClientListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class StatusCountsResponse(CamelModel):
    """Число клиентов в каждом статусе подписки (для дропдауна фильтра)."""

    trial: int = 0
    awaiting_card: int = 0
    active: int = 0
    past_due: int = 0
    blocked: int = 0
    cancelled: int = 0


class PhoneCheckResponse(CamelModel):
    owner_name: str | None = Field(default=None, description="ФИО владельца номера или null")


class StaffMember(CamelModel):
    id: uuid.UUID
    full_name: str
    phone: str
    role: str = Field(description="admin (владелец) | doctor (врач)")
    specialization: str | None = None

    @classmethod
    def from_row(cls, staff: StaffRow) -> StaffMember:
        return cls(
            id=staff.membership_id,
            full_name=staff.full_name,
            phone=staff.phone,
            role="admin" if staff.is_owner else "doctor",
            specialization=staff.specialization_name,
        )


class ClientPayment(CamelModel):
    id: str
    label: str
    date: datetime
    amount_tiyin: int
    status: str = Field(description="success | failed | refunded")


class ClientActivity(CamelModel):
    patients: int
    visits: int
    last_login: str


class ClientDetailResponse(ClientListItem):
    address: str | None = None
    business_phone: str | None = None
    language: str
    card_attached: bool = False
    specialization: str | None = None
    activity: ClientActivity | None = None
    staff: list[StaffMember]
    payments: list[ClientPayment] = Field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_detail(cls, detail: ClientDetail) -> ClientDetailResponse:
        clinic = detail.clinic
        sub = detail.subscription
        owner = next((s for s in detail.staff if s.is_owner), None)
        name = tariff_name_for(clinic.type)
        return cls(
            id=clinic.id,
            type=clinic.type,
            name=clinic.name,
            admin_full_name=owner.full_name if owner else None,
            admin_phone=owner.phone if owner else None,
            city_name=detail.city_name,
            tariff_name=name,
            tariff_label=name,
            seats_used=detail.seats_used,
            seats_limit=clinic.seats_limit,
            subscription_status=sub.status if sub else None,
            trial_day=_trial_day(
                sub.status if sub else None, sub.trial_started_at if sub else None
            ),
            created_at=clinic.created_at,
            current_period_end=sub.current_period_end if sub else None,
            address=clinic.address,
            business_phone=clinic.business_phone,
            language=clinic.lang.value,
            card_attached=bool(sub and sub.card_attached_at is not None),
            specialization=owner.specialization_name if owner else None,
            activity=None,  # блок активности соло — клиническое ядро вне скоупа
            staff=[StaffMember.from_row(s) for s in detail.staff],
            payments=[],  # журнал платежей клиента → Skrezo: биллинг отложен
            notes=clinic.support_notes or "",
        )


class CreateAccountRequest(CamelModel):
    type: ClinicType = Field(description="Тип аккаунта: solo|clinic")
    admin_full_name: str = Field(min_length=2, description="ФИО администратора/врача")
    admin_phone: str = Field(description="Телефон администратора (= логин)")
    seats: int | None = Field(default=None, ge=2, description="Мест (клиника; минимум 2)")
    city_id: uuid.UUID | None = Field(default=None, description="Город (обяз. для clinic)")
    name: str | None = Field(default=None, min_length=2, description="Название (обяз. для clinic)")
    address: str | None = None
    business_phone: str | None = Field(default=None, description="Телефон бизнеса (клиника)")
    language: Lang = Lang.RU


class AccountCreatedResponse(CamelModel):
    id: uuid.UUID
    type: ClinicType
    admin_phone: str = Field(description="Телефон владельца (= логин)")
    temp_password: str = Field(
        description="Временный пароль владельца — показать один раз и передать владельцу"
    )


class UpdateClientRequest(NotNullFieldsModel):
    """Правка карточки клиента: передаются только изменяемые поля.

    ``null`` осмыслен лишь для NULLABLE-колонок (снять адрес, город, заметки).
    Для полей, которые в БД NOT NULL, ``null`` — ошибка валидации (422): имена
    здесь ключей запроса (``name``, ``language`` → ``clinic.lang``).
    """

    NOT_NULL_FIELDS = ("name", "language")

    name: str | None = Field(default=None, min_length=2)
    address: str | None = None
    city_id: uuid.UUID | None = None
    business_phone: str | None = None
    language: Lang | None = None
    notes: str | None = None
    admin_full_name: str | None = Field(default=None, min_length=2, description="ФИО владельца")
    specialization_id: uuid.UUID | None = Field(default=None, description="Специализация (соло)")


class ChangeAdminPhoneRequest(CamelModel):
    new_phone: str = Field(description="Новый номер администратора (= логин владельца)")


class ArchiveClientRequest(CamelModel):
    reason: str | None = Field(default=None, description="Причина архивации")


class ChangeAdminPhoneResponse(CamelModel):
    ok: bool = True
    phone: str = Field(description="Установленный номер администратора (E.164)")


class DoctorCreateRequest(CamelModel):
    full_name: str = Field(min_length=2, description="ФИО врача")
    phone: str = Field(description="Телефон (= логин)")
    specialization_id: uuid.UUID = Field(description="Специализация (обязательна)")


class DoctorUpdateRequest(CamelModel):
    full_name: str | None = Field(default=None, min_length=2)
    phone: str | None = None
    specialization_id: uuid.UUID | None = None


class DoctorCreatedResponse(CamelModel):
    id: uuid.UUID = Field(description="membershipId созданного врача")
    temp_password: str = Field(
        description="Временный пароль врача — показать один раз и передать врачу"
    )


class DoctorRemovedResponse(CamelModel):
    ok: bool = True
    mode: str = Field(description="deactivated (soft) — доступ закрыт")


class PasswordResetResponse(CamelModel):
    temp_password: str = Field(
        description="Новый временный пароль — показать один раз и передать пользователю"
    )
