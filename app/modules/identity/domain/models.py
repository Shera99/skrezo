"""Доменные value-objects клиентского контура."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


class OtpPurpose(StrEnum):
    """Назначение кода подтверждения — вход или сброс пароля.

    Назначение — часть ключа кода, а не пометка «для порядка»: код, выпущенный
    на вход, не должен менять пароль, и наоборот. Иначе достаточно уговорить
    человека продиктовать «код для входа», чтобы забрать аккаунт себе.

    По этой же причине назначение попадает в текст сообщения: получатель должен
    видеть, что именно он подтверждает.
    """

    LOGIN = "login"
    PASSWORD_RESET = "reset"


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
