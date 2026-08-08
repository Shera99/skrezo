"""Канал доставки OTP-кода: общий контракт и ошибка недоставки.

Каналов несколько (Telegram Gateway, WhatsApp, SMS), и у каждого своя обёртка
над чужим API. Флоу входа о них не знает: он просит «доставь код на телефон»,
а какой канал сработал и был ли запасной — забота сервиса доставки.

Ошибка одна на все каналы (:class:`OtpDeliveryError`): вызывающему важно только
«код ушёл или нет». Детали (HTTP-код, тело ответа провайдера) уходят в лог —
в ответ клиенту им нельзя, там они превратились бы в подсказку атакующему.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.identity.domain.models import OtpPurpose
from app.shared.notifications.channels import MessageDeliveryError


class OtpDeliveryError(MessageDeliveryError):
    """Канал не смог доставить код.

    Частный случай общей недоставки: транспорты (WhatsApp, SMS) общие с
    уведомлениями, но цепочка OTP смотрит именно на свою ошибку — чтобы чужая
    недоставка случайно не превратилась в «код не отправлен».
    """


class OtpChannel(Protocol):
    """Канал доставки кода подтверждения."""

    #: Короткое имя канала — для настройки и логов (``telegram``, ``whatsapp``, ``sms``).
    name: str

    async def send(self, phone: str, code: str, purpose: OtpPurpose) -> None:
        """Отправить код на телефон в E.164; при неудаче — ``OtpDeliveryError``.

        Назначение канал сам не проверяет — оно нужно ему только для текста
        сообщения (Telegram Gateway с его собственным шаблоном игнорирует).
        """
        ...
