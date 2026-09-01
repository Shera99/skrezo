"""Контракт клиента WhatsApp Cloud API и общие типы.

Клиент — деталь конфигурации: боевой ходит в Graph API, лог-фолбэк работает
без кредов (см. ``factory``). Прикладной код зависит только от протокола.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class WhatsAppSendError(Exception):
    """Graph API отказал или не ответил — сообщение не отправлено."""


@dataclass(frozen=True, slots=True)
class SentMessage:
    """Результат отправки: идентификатор сообщения от Meta (``wamid…``)."""

    wa_message_id: str


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    """Скачанное медиа: содержимое и MIME, который сообщила Meta."""

    content: bytes
    mime: str


class WhatsAppCloudClient(Protocol):
    """Что умеет транспорт: отправить текст/шаблон, скачать медиа."""

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        """Отправить свободный текст (только в открытое 24-часовое окно)."""
        ...

    async def send_template(
        self,
        phone_number_id: str,
        to_phone: str,
        template_name: str,
        lang_code: str,
        variables: list[str],
    ) -> SentMessage:
        """Отправить одобренный шаблон (работает и вне окна)."""
        ...

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        """Скачать медиа по ``media_id``; ``None`` — скачивание недоступно.

        ``None`` — штатный ответ лог-фолбэка (нет кредов), а не ошибка: ссылка
        Meta живёт минуты, и «не скачали сейчас» значит «останется только id».
        """
        ...
