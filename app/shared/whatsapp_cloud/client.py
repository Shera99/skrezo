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
class TemplateInfo:
    """Шаблон из кабинета Meta (как его отдаёт Graph API)."""

    meta_name: str
    lang: str  # код языка Meta (ru, ky_KG…)
    category: str
    body_preview: str
    variables: list[str]  # имена плейсхолдеров тела: ["1", "2", …]
    status: str  # approved | pending | rejected | paused | disabled


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

    async def send_confirm_button(
        self, phone_number_id: str, to_phone: str, body: str, button_id: str, title: str
    ) -> SentMessage:
        """Сообщение с одной interactive-кнопкой (подтверждение записи, И3).

        Нажатие возвращается вебхуком как ``button_reply.id = button_id`` —
        детерминированный сигнал для обработчика, модель в нём не участвует.
        """
        ...

    async def check_token(self, phone_number_id: str) -> bool:
        """Токен ещё жив? (§13.5) Дешёвый запрос профиля номера в Graph API."""
        ...

    async def fetch_templates(self, waba_id: str) -> list[TemplateInfo] | None:
        """Шаблоны WABA из кабинета Meta; ``None`` — синхронизация недоступна.

        ``None`` (лог-фолбэк без кредов) отличён от пустого списка намеренно:
        пустой список — «в кабинете шаблонов нет» и стирает локальный каталог,
        ``None`` — «не смогли спросить», и трогать каталог нельзя.
        """
        ...
