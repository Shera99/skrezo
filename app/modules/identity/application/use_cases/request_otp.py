"""Use-case: запросить OTP-код на телефон.

Шаг 1 сразу трёх сценариев: вход соло-врача, его регистрация и восстановление
пароля сотрудником. Отличает их только назначение кода (:class:`OtpPurpose`) —
выпуск, доставка и обработка ошибок общие, и разводить их по классам значило бы
чинить доставку в двух местах.
"""

from __future__ import annotations

from app.core.errors import UpstreamError
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError
from app.shared.domain.value_objects import normalize_phone


class RequestOtpUseCase:
    def __init__(self, otp: OtpService) -> None:
        self._otp = otp

    async def execute(self, phone_raw: str, purpose: OtpPurpose = OtpPurpose.LOGIN) -> str:
        """Выпустить код на нормализованный телефон; вернуть его (для dev-выдачи).

        Ответ намеренно одинаков вне зависимости от того, есть ли уже аккаунт —
        чтобы не раскрывать зарегистрированные номера. Различие «вход или
        регистрация» решается только после проверки кода.

        По той же причине код на смену пароля уходит и на номер без аккаунта:
        «не отправили, потому что такого номера нет» — это ответ, по которому
        чужие номера перебираются пачкой. Расход на лишние SMS сдерживает
        rate-limit по паре IP+телефон.

        Если код не удалось доставить ни одним каналом — 502, а не «успех»:
        иначе пользователь ждёт SMS, которой не будет, и повторяет попытку,
        пока не упрётся в rate-limit. Причину наружу не выносим: детали ответа
        провайдера подсказывают, есть ли у номера Telegram.
        """
        phone = normalize_phone(phone_raw)
        try:
            return await self._otp.request_code(phone, purpose)
        except OtpDeliveryError as exc:
            raise UpstreamError(
                "Не удалось отправить код, попробуйте ещё раз", code="otp_delivery_failed"
            ) from exc
