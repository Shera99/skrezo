"""Доменные строки списка аккаунтов (платформенный контур).

Аккаунт глобальный: один человек может работать в нескольких клиниках, поэтому
его членства — вложенный список, а не колонки строки. Дублировать человека по
строке на клинику значило бы, что «сколько всего аккаунтов» больше не считается.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import Lang


@dataclass(frozen=True, slots=True)
class AccountMembershipRow:
    """Членство аккаунта в клинике — где он работает и работает ли ещё."""

    membership_id: uuid.UUID
    clinic_id: uuid.UUID
    clinic_name: str
    role_id: uuid.UUID
    role_name: str
    is_owner: bool
    active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AccountRow:
    id: uuid.UUID
    full_name: str
    phone: str
    lang: Lang
    onboarded: bool
    is_platform_admin: bool
    blocked_at: datetime | None
    blocked_reason: str | None
    created_at: datetime
    memberships: list[AccountMembershipRow]
