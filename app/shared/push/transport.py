"""Маршрутизация пуша по виду токена устройства, с фолбэком в лог.

Провайдер выбирается не по платформе, а по тому, какой токен прислало
приложение:

* токен FCM (Android **и** iOS через Firebase SDK) → канал ``fcm``; до iPhone
  его доводит сам Firebase, если в проект загружен APNs-ключ;
* «сырой» device token APNs (только iOS, hex-строка) → канал ``apns`` напрямую.

Так сервер принимает оба вида iOS-токенов: приложение на Firebase шлёт FCM-токен
и с ``platform=ios`` — раньше такой пуш уходил в APNs и отбрасывался как
``BadDeviceToken``. Нет канала для провайдера (не настроены ключи) — пуш уходит
в фолбэк (обычно лог).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, Literal

from app.shared.push.channels import PushChannel
from app.shared.push.message import PushMessage

Provider = Literal["fcm", "apns"]
FCM: Final = "fcm"
APNS: Final = "apns"

#: Device token APNs — 32 байта в hex (64 символа); Apple оставляет за собой
#: право удлинить его, поэтому длина «от 64». У токена FCM всегда есть «:» и
#: буквы вне a–f — спутать их нельзя.
_APNS_TOKEN = re.compile(r"[0-9a-fA-F]{64,}")


def is_apns_token(token: str) -> bool:
    """Это «сырой» device token APNs (hex), а не токен FCM."""
    return _APNS_TOKEN.fullmatch(token) is not None


def provider_for(platform: str, token: str) -> Provider:
    """Провайдер доставки: APNs — только iOS с hex-токеном APNs, иначе FCM."""
    if platform == "ios" and is_apns_token(token):
        return APNS
    return FCM


class PushTransport:
    """Отправляет пуш каналом провайдера, выбранного по токену устройства."""

    def __init__(self, channels: Mapping[str, PushChannel], fallback: PushChannel) -> None:
        #: провайдер (``fcm``/``apns``) → канал; чего нет — уходит в ``fallback``.
        self._channels = dict(channels)
        self._fallback = fallback

    def _channel(self, platform: str, token: str) -> PushChannel:
        return self._channels.get(provider_for(platform, token), self._fallback)

    async def send(self, platform: str, token: str, message: PushMessage) -> None:
        """Доставить пуш каналом провайдера; не настроен — фолбэк."""
        await self._channel(platform, token).send(token, message)

    def channel_name(self, platform: str, token: str) -> str:
        """Имя канала, которым уйдёт пуш на это устройство (для телеметрии)."""
        return self._channel(platform, token).name

    def provider_channel_name(self, provider: Provider) -> str:
        """Имя канала провайдера: живой (``fcm``/``apns``) или фолбэк (``log``)."""
        return self._channels.get(provider, self._fallback).name
