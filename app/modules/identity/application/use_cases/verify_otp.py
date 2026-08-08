"""Use-case: проверить OTP-код (шаг 2) — вход или переход к регистрации анкеты."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import BadRequestError, ConflictError
from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.application.services.session_factory import issue_session
from app.modules.identity.application.use_cases.login import LoginResult
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.shared.domain.value_objects import normalize_phone


@dataclass(slots=True)
class VerifyOtpResult:
    """Итог проверки кода: либо вход (``login``), либо билет на регистрацию."""

    login: LoginResult | None
    registration_token: str | None

    @property
    def status(self) -> str:
        return "authenticated" if self.login is not None else "registration_required"


class VerifyOtpUseCase:
    def __init__(
        self,
        otp: OtpService,
        accounts: AccountRepository,
        memberships: MembershipRepository,
        tokens: ClientTokenService,
    ) -> None:
        self._otp = otp
        self._accounts = accounts
        self._memberships = memberships
        self._tokens = tokens

    async def execute(self, phone_raw: str, code: str) -> VerifyOtpResult:
        """Проверить код и развести три случая.

        * аккаунта нет → билет регистрации (клиент показывает анкету);
        * аккаунт passwordless (соло) → вход, выдаём токены;
        * аккаунт с паролем (сотрудник клиники) → 409: этот номер входит по
          паролю, OTP его не логинит (иначе OTP обошёл бы пароль).
        """
        phone = normalize_phone(phone_raw)
        if not await self._otp.verify_code(phone, code, OtpPurpose.LOGIN):
            raise BadRequestError("Неверный или истёкший код", code="invalid_otp")

        account = await self._accounts.get_by_phone(phone)
        if account is None:
            token = await self._otp.issue_registration_ticket(phone)
            return VerifyOtpResult(login=None, registration_token=token)
        if account.password_hash is not None:
            raise ConflictError("Этот номер входит по паролю", code="use_password_login")

        login = await issue_session(account, self._memberships, self._tokens)
        return VerifyOtpResult(login=login, registration_token=None)
