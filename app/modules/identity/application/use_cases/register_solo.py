"""Use-case: регистрация соло-врача (шаг 3) — анкета после подтверждённого OTP."""

from __future__ import annotations

import uuid

from app.core.errors import BadRequestError
from app.modules.clinics.application.services.provisioning_service import (
    ProvisionCommand,
    ProvisioningService,
)
from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.application.services.session_factory import issue_session
from app.modules.identity.application.use_cases.login import LoginResult
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.shared.db.enums import ClinicType, Lang


class RegisterSoloUseCase:
    def __init__(
        self,
        otp: OtpService,
        provisioning: ProvisioningService,
        accounts: AccountRepository,
        memberships: MembershipRepository,
        tokens: ClientTokenService,
    ) -> None:
        self._otp = otp
        self._provisioning = provisioning
        self._accounts = accounts
        self._memberships = memberships
        self._tokens = tokens

    async def execute(
        self,
        registration_token: str,
        full_name: str,
        specialization_id: uuid.UUID,
        city_id: uuid.UUID,
        lang: Lang = Lang.RU,
    ) -> LoginResult:
        """Создать соло-аккаунт (passwordless), 30-дневный триал и сразу войти.

        Телефон берётся из билета регистрации, выданного проверкой кода, — клиент
        его не передаёт и подменить не может. Билет гасится только после успешного
        провижининга: ошибка анкеты (неизвестный город/специализация) позволяет
        повторить без нового OTP.
        """
        phone = await self._otp.read_registration_ticket(registration_token)
        if phone is None:
            raise BadRequestError(
                "Регистрация не подтверждена или истекла", code="registration_not_verified"
            )

        command = ProvisionCommand(
            type=ClinicType.SOLO,
            admin_full_name=full_name,
            admin_phone=phone,
            city_id=city_id,
            specialization_id=specialization_id,
            passwordless=True,
            lang=lang,
        )
        result = await self._provisioning.provision(command)  # атомарно + commit
        await self._otp.discard_registration_ticket(registration_token)

        return await issue_session(result.account, self._memberships, self._tokens)
