"""OTP-сервис: выпуск, проверка кода и одноразовый билет регистрации.

Всё состояние — в Redis с TTL (код, счётчик неверных попыток, билет регистрации).
Коды и билеты генерируются криптостойко (``secrets``), сравнение — через
``compare_digest`` (защита от тайминг-атак). Телефон везде — уже нормализованный
E.164 (нормализует вызывающий слой).

Ключ кода включает **назначение** (:class:`OtpPurpose`): вход и смена пароля
живут в разных ключах и разными счётчиками попыток. Один общий код на всё
означал бы, что «продиктуйте код для входа» открывает и смену пароля.
"""

from __future__ import annotations

import secrets

from redis.asyncio import Redis

from app.core.config import settings
from app.core.errors import TooManyRequestsError
from app.modules.identity.application.services.otp_delivery_service import OtpDeliveryService
from app.modules.identity.domain.models import OtpPurpose

_CODE_KEY = "auth:otp:code:{}:{}"
_ATTEMPTS_KEY = "auth:otp:attempts:{}:{}"
_TICKET_KEY = "auth:otp:reg:{}"
_DIGITS = "0123456789"


class OtpService:
    def __init__(self, redis: Redis, delivery: OtpDeliveryService) -> None:
        self._redis = redis
        self._delivery = delivery

    async def request_code(self, phone: str, purpose: OtpPurpose) -> str:
        """Выпустить и «отправить» код; вернуть его (для dev-режима/логов доставки).

        Новый запрос перезаписывает прежний код **того же назначения** и обнуляет
        его счётчик попыток — предыдущий код становится недействительным.
        """
        code = "".join(secrets.choice(_DIGITS) for _ in range(settings.otp_length))
        await self._redis.set(
            _CODE_KEY.format(purpose.value, phone), code, ex=settings.otp_ttl_seconds
        )
        await self._redis.delete(_ATTEMPTS_KEY.format(purpose.value, phone))
        await self._delivery.send_code(phone, code, purpose)
        return code

    async def verify_code(self, phone: str, code: str, purpose: OtpPurpose) -> bool:
        """Проверить код. Неверные попытки считаются; после лимита код сжигается.

        Возвращает ``True`` при совпадении (код при этом расходуется). Слишком
        много неверных попыток → 429 (защита от перебора 6 цифр). Код чужого
        назначения не подходит — он лежит в другом ключе.
        """
        code_key = _CODE_KEY.format(purpose.value, phone)
        stored = await self._redis.get(code_key)
        if stored is None:
            return False  # кода нет или истёк

        attempts_key = _ATTEMPTS_KEY.format(purpose.value, phone)
        attempts = await self._redis.incr(attempts_key)
        if attempts == 1:
            await self._redis.expire(attempts_key, settings.otp_ttl_seconds)
        if attempts > settings.otp_max_attempts:
            # Сжигаем код, чтобы перебор нельзя было продолжить после ожидания.
            await self._redis.delete(code_key)
            raise TooManyRequestsError(
                "Слишком много попыток, запросите код заново", code="otp_too_many_attempts"
            )

        if not secrets.compare_digest(stored, code):
            return False

        await self._redis.delete(code_key)
        await self._redis.delete(attempts_key)
        return True

    async def issue_registration_ticket(self, phone: str) -> str:
        """Выдать одноразовый билет: подтверждённый телефон → регистрация анкеты."""
        token = secrets.token_urlsafe(24)
        await self._redis.set(
            _TICKET_KEY.format(token), phone, ex=settings.otp_registration_ttl_seconds
        )
        return token

    async def read_registration_ticket(self, token: str) -> str | None:
        """Вернуть телефон по билету, НЕ погашая его.

        Билет гасится (``discard_registration_ticket``) только после успешного
        провижининга — иначе ошибка валидации анкеты сожгла бы билет и вынудила
        заново проходить OTP.
        """
        phone: str | None = await self._redis.get(_TICKET_KEY.format(token))
        return phone

    async def discard_registration_ticket(self, token: str) -> None:
        """Погасить билет (после успешной регистрации — одноразовость)."""
        await self._redis.delete(_TICKET_KEY.format(token))
