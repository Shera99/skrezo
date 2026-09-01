"""Маршрутизация пуша по платформе устройства, с фолбэком в лог.

У устройства ровно одна платформа, поэтому это не цепочка каналов с перебором
(как у текстовой доставки), а выбор одного канала по ``platform``. Нет канала для
платформы (не настроен провайдер) — уходит в фолбэк (обычно лог).
"""

from __future__ import annotations

from collections.abc import Mapping

from app.shared.push.channels import PushChannel
from app.shared.push.message import PushMessage


class PushTransport:
    """Отправляет пуш каналом, выбранным по платформе устройства."""

    def __init__(self, channels: Mapping[str, PushChannel], fallback: PushChannel) -> None:
        #: platform (``ios``/``android``) → канал; чего нет — уходит в ``fallback``.
        self._channels = dict(channels)
        self._fallback = fallback

    async def send(self, platform: str, token: str, message: PushMessage) -> None:
        """Доставить пуш каналом платформы; неизвестная платформа — фолбэк."""
        channel = self._channels.get(platform, self._fallback)
        await channel.send(token, message)

    def channel_name(self, platform: str) -> str:
        """Имя канала, которым уйдёт пуш этой платформы (для телеметрии)."""
        return self._channels.get(platform, self._fallback).name
