"""Схемы списка аккаунтов (платформенный контур)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.accounts.domain.models import AccountMembershipRow, AccountRow
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import Lang


class AccountMembershipOut(CamelModel):
    """Где работает аккаунт и работает ли ещё."""

    membership_id: uuid.UUID
    clinic_id: uuid.UUID
    clinic_name: str
    role_id: uuid.UUID
    role_name: str = Field(examples=["Работник"])
    is_owner: bool = Field(description="Владелец клиники — его нельзя исключить")
    active: bool = Field(
        description=(
            "false — исключён из этой клиники: доступа к ней нет "
            "(403 `membership_revoked`), история приёмов сохранена"
        )
    )
    created_at: datetime = Field(description="Когда человека добавили в эту клинику")

    @classmethod
    def from_row(cls, row: AccountMembershipRow) -> AccountMembershipOut:
        return cls(
            membership_id=row.membership_id,
            clinic_id=row.clinic_id,
            clinic_name=row.clinic_name,
            role_id=row.role_id,
            role_name=row.role_name,
            is_owner=row.is_owner,
            active=row.active,
            created_at=row.created_at,
        )


class AccountListItem(CamelModel):
    """Аккаунт платформы со всеми его клиниками.

    Аккаунт глобальный: сам по себе он не удаляется и не «исключается» —
    исключают из клиники, то есть членство. Поэтому статус лежит на каждой
    записи `memberships`, а `hasActiveMembership` — это сводка по ним для колонки
    списка.
    """

    id: uuid.UUID
    full_name: str
    phone: str = Field(description="Телефон = логин (E.164)", examples=["+996700123456"])
    lang: Lang
    onboarded: bool
    is_platform_admin: bool = Field(
        description="Команда Skrezo: входит по паролю, членств в клиниках не имеет"
    )
    has_active_membership: bool = Field(
        description=(
            "Есть хотя бы одна клиника, где человек ещё работает. У команды "
            "Skrezo всегда false — членств у неё нет по устройству"
        )
    )
    memberships: list[AccountMembershipOut] = Field(
        description="Клиники аккаунта: сначала действующие, затем те, откуда исключён"
    )
    created_at: datetime

    @classmethod
    def from_row(cls, row: AccountRow) -> AccountListItem:
        return cls(
            id=row.id,
            full_name=row.full_name,
            phone=row.phone,
            lang=row.lang,
            onboarded=row.onboarded,
            is_platform_admin=row.is_platform_admin,
            has_active_membership=any(m.active for m in row.memberships),
            memberships=[AccountMembershipOut.from_row(m) for m in row.memberships],
            created_at=row.created_at,
        )


class AccountsResponse(CamelModel):
    list: list[AccountListItem]
    current_page: int
    page_size: int = 25
    total_count: int
