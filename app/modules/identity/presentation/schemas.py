"""Схемы запросов/ответов клиентской аутентификации."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.modules.identity.domain.models import MembershipContext
from app.shared.api.schemas import CamelModel


class LoginRequest(CamelModel):
    phone: str = Field(description="Телефон (= логин), любой формат — нормализуется в E.164")
    password: str = Field(min_length=1, description="Пароль аккаунта")


class ChangePasswordRequest(CamelModel):
    current_password: str = Field(min_length=1, description="Текущий (в т.ч. временный) пароль")
    new_password: str = Field(min_length=8, max_length=128, description="Новый пароль (мин. 8)")


class OkResponse(CamelModel):
    ok: bool = True


class AccountOut(CamelModel):
    id: uuid.UUID
    full_name: str
    phone: str
    lang: str
    onboarded: bool


class ClinicMembershipOut(CamelModel):
    clinic_id: uuid.UUID
    clinic_name: str
    role_id: uuid.UUID
    role_name: str
    is_owner: bool

    @classmethod
    def from_context(cls, membership: MembershipContext) -> ClinicMembershipOut:
        return cls(
            clinic_id=membership.clinic_id,
            clinic_name=membership.clinic_name,
            role_id=membership.role_id,
            role_name=membership.role_name,
            is_owner=membership.is_owner,
        )


class ClientTokensOut(CamelModel):
    access_token: str
    refresh_token: str
    expires_in: int


class ClientTokenResponse(ClientTokensOut):
    account: AccountOut
    clinics: list[ClinicMembershipOut]
    active_clinic_id: uuid.UUID | None = Field(
        default=None, description="Клиника в токене (если единственная)"
    )
    must_change_password: bool = Field(
        default=False, description="Пароль временный — требуется смена перед работой"
    )
    is_platform_admin: bool = Field(
        default=False, description="Сотрудник команды Skrezo (доступ к админ-панели /admin/*)"
    )


class RefreshRequest(CamelModel):
    refresh_token: str


class AccessResponse(CamelModel):
    access_token: str
    expires_in: int


class SelectClinicRequest(CamelModel):
    clinic_id: uuid.UUID = Field(description="Клиника, в контексте которой работать")


class MeResponse(CamelModel):
    account: AccountOut
    active_clinic: ClinicMembershipOut | None = None
    permissions: list[str] = Field(description="Ключи прав в активной клинике")
    clinics: list[ClinicMembershipOut]
    is_platform_admin: bool = Field(
        default=False, description="Сотрудник команды Skrezo (доступ к /admin/*)"
    )
