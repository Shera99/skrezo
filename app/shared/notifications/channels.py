"""Канал текстового сообщения: общий контракт и ошибка недоставки.

Отличие от OTP-каналов (:mod:`app.modules.identity.infrastructure.otp`) — в
содержимом: там провайдеру уходит **код**, здесь произвольный текст. Разделение
не формальное: Telegram Gateway умеет только коды по своему шаблону и текстовым
каналом быть не может, поэтому уведомления ходят через WhatsApp и SMS.

Транспорты ничего не знают о настройках: адреса, логины и таймаут им передаёт
фабрика. Так один и тот же транспорт годится и OTP-коду, и уведомлению, и его
можно собрать в тесте, не трогая глобальные ``settings``.
"""

from __future__ import annotations

from typing import Protocol


class MessageDeliveryError(Exception):
    """Канал не смог доставить сообщение."""


class TextChannel(Protocol):
    """Канал доставки произвольного текста на телефон."""

    #: Короткое имя канала — для настройки и логов (``whatsapp``, ``sms``).
    name: str

    async def send_text(self, phone: str, text: str) -> None:
        """Отправить текст на телефон в E.164; при неудаче — ``MessageDeliveryError``."""
        ...


class ChatChannel(Protocol):
    """Канал доставки текста в чат: адресат задан настройкой, а не номером."""

    #: Короткое имя канала — для логов (``telegram-chat``).
    name: str

    async def send(self, text: str) -> None:
        """Отправить текст в чат; при неудаче — ``MessageDeliveryError``."""
        ...
