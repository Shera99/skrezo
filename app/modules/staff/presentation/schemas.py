"""Схемы управления сотрудниками (клиентская сторона)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.staff.domain.models import StaffMember
from app.shared.api.schemas import CamelModel


class StaffMemberOut(CamelModel):
    membership_id: uuid.UUID
    account_id: uuid.UUID
    full_name: str
    phone: str
    role_id: uuid.UUID
    role_name: str
    is_owner: bool
    specialization_id: uuid.UUID | None = None
    active: bool
    created_at: datetime

    @classmethod
    def from_member(cls, member: StaffMember) -> StaffMemberOut:
        return cls(
            membership_id=member.membership_id,
            account_id=member.account_id,
            full_name=member.full_name,
            phone=member.phone,
            role_id=member.role_id,
            role_name=member.role_name,
            is_owner=member.is_owner,
            specialization_id=member.specialization_id,
            active=member.active,
            created_at=member.created_at,
        )


class AddStaffRequest(CamelModel):
    full_name: str = Field(min_length=2, description="ФИО врача")
    phone: str = Field(description="Телефон (= логин)")
    specialization_id: uuid.UUID = Field(description="Специализация (обязательна)")


class UpdateStaffRequest(CamelModel):
    full_name: str | None = Field(default=None, min_length=2)
    phone: str | None = None
    specialization_id: uuid.UUID | None = None


class StaffCreatedResponse(CamelModel):
    membership_id: uuid.UUID
    temp_password: str = Field(
        description="Временный пароль работника — показать один раз и передать работнику"
    )


class StaffRemovedResponse(CamelModel):
    ok: bool = True
    mode: str


class StaffPasswordResetResponse(CamelModel):
    temp_password: str = Field(
        description="Новый временный пароль работника — показать один раз и передать"
    )
