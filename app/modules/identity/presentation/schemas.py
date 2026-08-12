"""Схемы запросов/ответов клиентской аутентификации."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.modules.identity.application.use_cases.login import LoginResult
from app.modules.identity.domain.models import MembershipContext
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import Lang


class LoginRequest(CamelModel):
    """Вход по паролю — только для аккаунтов команды Skrezo."""

    phone: str = Field(description="Телефон (= логин), любой формат — нормализуется в E.164")
    password: str = Field(min_length=1, description="Пароль аккаунта команды Skrezo")


class ChangePasswordRequest(CamelModel):
    current_password: str = Field(min_length=1, description="Текущий (в т.ч. временный) пароль")
    new_password: str = Field(min_length=8, max_length=128, description="Новый пароль (мин. 8)")


class OkResponse(CamelModel):
    ok: bool = True


class AccountOut(CamelModel):
    """Свой аккаунт.

    ``blocked`` здесь всегда false: заблокированный не получит ни токен на входе,
    ни ответ на авторизованный запрос. Поле оставлено для единообразия — там, где
    отдаётся аккаунт, отдаётся и его блокировка.
    """

    id: uuid.UUID
    full_name: str
    phone: str
    lang: str
    onboarded: bool
    blocked: bool = Field(
        default=False, description="Аккаунт заблокирован командой Skrezo (доступ отобран целиком)"
    )


class ClinicMembershipOut(CamelModel):
    membership_id: uuid.UUID = Field(
        description="Членство аккаунта в этой клинике — «свой» id для расписания и записей"
    )
    clinic_id: uuid.UUID
    clinic_name: str
    role_id: uuid.UUID
    role_name: str
    is_owner: bool

    @classmethod
    def from_context(cls, membership: MembershipContext) -> ClinicMembershipOut:
        return cls(
            membership_id=membership.membership_id,
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
        default=False,
        description=(
            "Пароль временный — требуется смена перед работой. Осмыслен только для "
            "команды Skrezo: у остальных пароля нет, и здесь всегда false"
        ),
    )
    is_platform_admin: bool = Field(
        default=False, description="Сотрудник команды Skrezo (доступ к админ-панели /admin/*)"
    )

    @classmethod
    def from_login_result(cls, result: LoginResult) -> ClientTokenResponse:
        return cls(
            access_token=result.tokens.access_token,
            refresh_token=result.tokens.refresh_token,
            expires_in=result.tokens.expires_in,
            account=AccountOut.model_validate(result.account),
            clinics=[ClinicMembershipOut.from_context(m) for m in result.memberships],
            active_clinic_id=result.active_clinic_id,
            must_change_password=result.must_change_password,
            is_platform_admin=result.is_platform_admin,
        )


class OtpRequestRequest(CamelModel):
    """Шаг 1: запрос кода на телефон (основной вход — клиники и соло-врачи)."""

    phone: str = Field(description="Телефон, любой формат — нормализуется в E.164")


class OtpRequestResponse(CamelModel):
    """Код выпущен и отправлен (для одинакового ответа независимо от наличия аккаунта)."""

    sent: bool = True
    dev_code: str | None = Field(
        default=None,
        description="Код в dev-режиме (канал доставки не выбран); в проде всегда null",
    )


class ForgotPasswordRequest(CamelModel):
    """Шаг 1 восстановления доступа: код на телефон аккаунта команды Skrezo."""

    phone: str = Field(description="Телефон аккаунта, любой формат — нормализуется в E.164")


class ResetPasswordRequest(CamelModel):
    """Шаг 2 восстановления доступа: код из сообщения + новый пароль."""

    phone: str = Field(description="Тот же телефон, что в запросе кода")
    code: str = Field(min_length=4, max_length=8, description="Код из сообщения")
    new_password: str = Field(min_length=8, max_length=128, description="Новый пароль (мин. 8)")


class OtpVerifyRequest(CamelModel):
    """Шаг 2: подтверждение кода."""

    phone: str = Field(description="Тот же телефон, что в запросе кода")
    code: str = Field(min_length=4, max_length=8, description="Код из сообщения")


class OtpVerifyResponse(CamelModel):
    """Итог проверки кода: вход (``auth``) либо переход к анкете (``registrationToken``)."""

    status: str = Field(description="authenticated — вошёл; registration_required — нужна анкета")
    registration_token: str | None = Field(
        default=None, description="Билет для POST /auth/solo/register (одноразовый)"
    )
    auth: ClientTokenResponse | None = Field(
        default=None, description="Токены и профиль при status=authenticated"
    )


class SoloRegisterRequest(CamelModel):
    """Шаг 3: анкета соло-врача после подтверждённого OTP."""

    registration_token: str = Field(description="Билет из ответа /auth/otp/verify")
    full_name: str = Field(min_length=2, description="Имя врача")
    specialization_id: uuid.UUID = Field(description="Профессия из GET /specializations")
    city_id: uuid.UUID = Field(description="Город из GET /cities")


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


class UpdateProfileRequest(NotNullFieldsModel):
    """Правка своего профиля: имя и язык интерфейса.

    Телефона здесь нет намеренно — он же логин, и менять его себе сотрудник не
    должен. Это делает владелец или менеджер через `PUT /clinic/staff/{id}`.
    """

    NOT_NULL_FIELDS = ("fullName", "lang")

    full_name: str | None = Field(
        default=None, min_length=2, max_length=200, description="ФИО целиком"
    )
    lang: Lang | None = Field(default=None, description="Язык интерфейса: kg|kz|ru|en")
