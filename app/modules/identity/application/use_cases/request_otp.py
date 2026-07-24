"""Use-case: запросить OTP-код на телефон (шаг 1 входа/регистрации соло-врача)."""

from __future__ import annotations

from app.modules.identity.application.services.otp_service import OtpService
from app.shared.domain.value_objects import normalize_phone


class RequestOtpUseCase:
    def __init__(self, otp: OtpService) -> None:
        self._otp = otp

    async def execute(self, phone_raw: str) -> str:
        """Выпустить код на нормализованный телефон; вернуть его (для dev-выдачи).

        Ответ намеренно одинаков вне зависимости от того, есть ли уже аккаунт —
        чтобы не раскрывать зарегистрированные номера. Различие «вход или
        регистрация» решается только после проверки кода.
        """
        phone = normalize_phone(phone_raw)
        return await self._otp.request_code(phone)
