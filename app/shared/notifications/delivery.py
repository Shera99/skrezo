"""Доставка текста на телефон: цепочка каналов с переходом при неудаче.

Устроена так же, как доставка OTP, и по той же причине: у части номеров нет
WhatsApp, и без автоматического перехода на SMS уведомление до них просто не
дойдёт. Порядок задаёт ``NOTIFY_CHANNELS`` — сначала бесплатное, потом платное.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from app.shared.notifications.channels import MessageDeliveryError, TextChannel

logger = structlog.get_logger(__name__)


class TextDeliveryService:
    """Отправка текста по первому сработавшему каналу."""

    def __init__(self, channels: Sequence[TextChannel]) -> None:
        self._channels = channels

    async def send(self, phone: str, text: str) -> None:
        """Отправить текст; ни один канал не справился — ``MessageDeliveryError``."""
        failures: list[str] = []
        for channel in self._channels:
            try:
                await channel.send_text(phone, text)
            except MessageDeliveryError as exc:
                failures.append(f"{channel.name}: {exc}")
                logger.warning("notify_channel_failed", channel=channel.name, error=str(exc))
                continue
            logger.info("notification_sent", channel=channel.name, phone=phone)
            return

        raise MessageDeliveryError("; ".join(failures) or "нет настроенных каналов доставки")
