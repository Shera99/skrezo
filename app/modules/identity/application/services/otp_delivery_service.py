"""Доставка OTP-кода: цепочка каналов с переходом к следующему при неудаче.

Флоу входа знает только «доставь код на телефон». Какой канал сработал —
Telegram, WhatsApp или SMS — решает эта цепочка, заданная настройкой
``OTP_CHANNELS`` в порядке приоритета.

Переход к следующему каналу — не украшение, а основной сценарий: у половины
номеров нет Telegram, и без автоматического перехода вход для них просто не
работал бы. Порядок «сначала бесплатное, потом платное» экономит SMS.

Ошибку последнего канала наружу не отдаём: клиенту достаточно «код не удалось
отправить», а тело ответа провайдера в API превратилось бы в подсказку
атакующему (существует ли номер в Telegram, что отвечает шлюз).
"""

from __future__ import annotations

import structlog

from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.infrastructure.otp.channels import OtpChannel, OtpDeliveryError

logger = structlog.get_logger(__name__)


class OtpDeliveryService:
    """Отправка кода подтверждения по первому сработавшему каналу."""

    def __init__(self, channels: list[OtpChannel]) -> None:
        self._channels = channels

    async def send_code(
        self, phone: str, code: str, purpose: OtpPurpose = OtpPurpose.LOGIN
    ) -> None:
        """Отправить код; ни один канал не справился — ``OtpDeliveryError``."""
        failures: list[str] = []
        for channel in self._channels:
            try:
                await channel.send(phone, code, purpose)
            except OtpDeliveryError as exc:
                # Неудача одного канала — рабочая ситуация (нет Telegram у номера,
                # шлюз недоступен): пробуем следующий и запоминаем причину.
                failures.append(f"{channel.name}: {exc}")
                logger.warning("otp_channel_failed", channel=channel.name, error=str(exc))
                continue
            logger.info("otp_code_sent", channel=channel.name, phone=phone, purpose=purpose.value)
            return

        logger.error("otp_delivery_failed", phone=phone, failures=failures)
        raise OtpDeliveryError("; ".join(failures) or "нет настроенных каналов доставки")
