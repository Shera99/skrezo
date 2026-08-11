"""Use-case: смена пароля по коду из SMS — «забыл пароль» (шаг 2).

Отличие от :mod:`change_password` в том, кто подтверждает право сменить пароль:
там — текущий пароль, здесь — владение номером телефона. Оба пути актуальны
только для команды Skrezo: пароль есть лишь у аккаунтов ``is_platform_admin``.
Забывший пароль админ, потерявший ещё и номер, восстанавливается скриптом
``app/scripts/create_platform_user.py``.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.shared.domain.value_objects import normalize_phone


class ResetPasswordUseCase:
    def __init__(
        self,
        session: AsyncSession,
        otp: OtpService,
        accounts: AccountRepository,
        tokens: ClientTokenService,
    ) -> None:
        self._session = session
        self._otp = otp
        self._accounts = accounts
        self._tokens = tokens

    async def execute(self, phone_raw: str, *, code: str, new_password: str) -> None:
        """Проверить код назначения ``reset`` и поставить новый пароль.

        Подходит только код, выпущенный ``/auth/password/forgot``: код входа
        лежит под другим ключом и сюда не годится — иначе достаточно было бы
        уговорить человека продиктовать «код для входа».

        Аккаунта нет → 404, аккаунт не из команды Skrezo → 409: ему пароль
        сбрасывать нечего, он входит по коду. Оба ответа приходят уже после
        верного кода, то есть тому, кто владеет номером, — перебирать чужие
        номера ими нельзя.

        Успешный сброс выкидывает все refresh-сессии аккаунта: пароль меняют в
        том числе тогда, когда доступ уже у кого-то чужого, и оставлять ему
        открытую сессию бессмысленно.
        """
        phone = normalize_phone(phone_raw)
        if not await self._otp.verify_code(phone, code, OtpPurpose.PASSWORD_RESET):
            raise BadRequestError("Неверный или истёкший код", code="invalid_otp")

        account = await self._accounts.get_by_phone(phone)
        if account is None:
            raise NotFoundError("Аккаунт не найден", code="account_not_found")
        if not account.is_platform_admin:
            raise ConflictError("Этот номер входит по коду из SMS", code="use_otp_login")

        account.password_hash = hash_password(new_password)
        # Пароль выбрал сам владелец номера — временным он не считается.
        account.must_change_password = False
        await self._session.commit()
        # Только после коммита: не сложилась запись — не должны падать и сессии.
        await self._tokens.revoke_all(str(account.id))
