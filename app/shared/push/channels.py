"""Контракт канала доставки пуша и ошибки доставки.

Канал знает одного провайдера (FCM — Android и iOS-токены Firebase, APNs —
«сырые» iOS-токены; выбор — :func:`~app.shared.push.transport.provider_for`) и умеет одно —
доставить :class:`~app.shared.push.message.PushMessage` на токен. Настроек он не
читает: адреса и ключи ему передаёт фабрика (:mod:`app.shared.push.factory`),
поэтому канал собирается и в тесте, не трогая глобальные ``settings``.
"""

from __future__ import annotations

from typing import Protocol

from app.shared.push.message import PushMessage


class PushDeliveryError(Exception):
    """Канал не смог доставить пуш — временная ошибка, задачу можно повторить."""


class DeadTokenError(PushDeliveryError):
    """Токен недействителен (``NotRegistered``/``Unregistered``).

    Устройство удаляется без ретраев: повторять доставку на мёртвый токен
    бессмысленно, а держать его в таблице — растить стоимость каждой рассылки.
    """


class PushChannel(Protocol):
    """Канал доставки пуша одного провайдера."""

    #: Короткое имя канала — для логов (``fcm``, ``apns``, ``log``).
    name: str

    async def send(self, token: str, message: PushMessage) -> None:
        """Доставить пуш на токен.

        Временная ошибка провайдера — :class:`PushDeliveryError` (повтор);
        мёртвый токен — :class:`DeadTokenError` (устройство снять, не повторять).
        """
        ...
