"""Доменная структура сотрудника клиники (для чтения)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StaffMember:
    membership_id: uuid.UUID
    account_id: uuid.UUID
    full_name: str
    phone: str
    role_id: uuid.UUID
    role_name: str
    is_owner: bool
    specialization_id: uuid.UUID | None
    active: bool
    created_at: datetime
