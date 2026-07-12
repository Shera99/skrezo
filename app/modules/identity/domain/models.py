"""Доменные value-objects клиентского контура."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MembershipContext:
    """Членство аккаунта в клинике с ролью (для выбора клиники и токена)."""

    membership_id: uuid.UUID
    clinic_id: uuid.UUID
    clinic_name: str
    role_id: uuid.UUID
    role_name: str
    role_key: str | None
    is_owner: bool
