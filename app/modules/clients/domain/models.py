"""Доменные структуры чтения для админского раздела «Клиенты»."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import ClinicType, SubscriptionStatus
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.subscription import Subscription


@dataclass(frozen=True, slots=True)
class ClientListRow:
    id: uuid.UUID
    type: ClinicType
    name: str
    admin_full_name: str | None
    admin_phone: str | None
    city_id: uuid.UUID | None
    city_name: str | None
    seats_used: int
    seats_limit: int | None
    subscription_status: SubscriptionStatus | None
    trial_started_at: datetime | None
    current_period_end: datetime | None
    card_attached_at: datetime | None
    created_at: datetime


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
    created_at: datetime


@dataclass(slots=True)
class ClientDetail:
    clinic: Clinic
    city_name: str | None
    subscription: Subscription | None
    staff: list[StaffRow]
    seats_used: int
