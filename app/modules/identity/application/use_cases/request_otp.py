"""Use-case: запросить OTP-код на телефон (шаг 1 входа/регистрации соло-врача)."""

from __future__ import annotations

from app.core.errors import UpstreamError
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError
from app.shared.domain.value_objects import normalize_phone


class RequestOtpUseCase:
    def __init__(self, otp: OtpService) -> None:
        self._otp = otp

    async def execute(self, phone_raw: str) -> str:
        """Выпустить код на нормализованный телефон; вернуть его (для dev-выдачи).

        Ответ намеренно одинаков вне зависимости от того, есть ли уже аккаунт —
        чтобы не раскрывать зарегистрированные номера. Различие «вход или
        регистрация» решается только после проверки кода.

        Если код не удалось доставить ни одним каналом — 502, а не «успех»:
        иначе пользователь ждёт SMS, которой не будет, и повторяет попытку,
        пока не упрётся в rate-limit. Причину наружу не выносим: детали ответа
        провайдера подсказывают, есть ли у номера Telegram.
        """
        phone = normalize_phone(phone_raw)
        try:
            return await self._otp.request_code(phone)
        except OtpDeliveryError as exc:
            raise UpstreamError(
                "Не удалось отправить код, попробуйте ещё раз", code="otp_delivery_failed"
            ) from exc
