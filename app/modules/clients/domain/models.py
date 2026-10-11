"""Доменные структуры чтения для админского раздела «Клиенты»."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import ClinicType, SubscriptionStatus
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_custom_plan import ClinicCustomPlan
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.plan_request import PlanRequest
from app.shared.db.models.subscription import Subscription


@dataclass(frozen=True, slots=True)
class ClientListRow:
    id: uuid.UUID
    type: ClinicType
    name: str
    admin_full_name: str | None
    admin_phone: str | None
    admin_blocked_at: datetime | None
    city_id: uuid.UUID | None
    city_name: str | None
    seats_used: int
    seats_limit: int | None
    subscription_status: SubscriptionStatus | None
    trial_started_at: datetime | None
    current_period_end: datetime | None
    card_attached_at: datetime | None
    created_at: datetime
    #: Пациентов клиники (по приёмам) и нетто кассы за всё время.
    patients_count: int = 0
    revenue_tiyin: int = 0


@dataclass(frozen=True, slots=True)
class StaffRow:
    membership_id: uuid.UUID
    account_id: uuid.UUID
    full_name: str
    phone: str
    role_id: uuid.UUID
    role_key: str | None
    role_name: str
    is_owner: bool
    specialization_id: uuid.UUID | None
    specialization_name: str | None
    active: bool
    blocked_at: datetime | None
    created_at: datetime


@dataclass(slots=True)
class ClientDetail:
    clinic: Clinic
    city_name: str | None
    subscription: Subscription | None
    staff: list[StaffRow]
    seats_used: int
    patients_count: int = 0
    visits_count: int = 0
    revenue_tiyin: int = 0
    #: Привязка номера WhatsApp Cloud API; ``None`` — номер не подключён.
    whatsapp: ClinicWhatsApp | None = None
    #: Спецплан Skrezo; ``None`` — клиника на публичных пакетах.
    custom_plan: ClinicCustomPlan | None = None
    #: Открытая заявка клиники на условия сверх тарифов.
    open_plan_request: PlanRequest | None = None


@dataclass(frozen=True, slots=True)
class ClientCard:
    """Клиент глазами команды Skrezo: кто это и как связаться (карточка в канал).

    Собирается одним запросом на пачку клиник — джоба конца триала шлёт карточки
    всем перешедшим сразу. Названия города и специализации — по-русски: карточки
    читает команда, а не клиника.

    Счётчики активности — за всё время, но у клиники с только что кончившимся
    триалом «всё время» и есть триал: оплаченного периода у неё ещё не было.
    """

    clinic_id: uuid.UUID
    name: str
    type: ClinicType
    city_name: str | None
    address: str | None
    business_phone: str | None
    #: Владелец; ``None`` — активного владельца нет (после архивации бывает).
    owner_full_name: str | None
    owner_phone: str | None
    owner_specialization: str | None
    registered_at: datetime
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    #: Активные сотрудники вместе с владельцем.
    staff_count: int
    patients_count: int
    appointments_count: int
