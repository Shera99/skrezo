"""Транспорт «сообщение в Telegram-чат» через Bot API.

Стоит особняком от каналов на телефон: адресат тут не номер, а чат (общий чат
команды Skrezo), поэтому и контракт другой — ``send(text)`` без телефона.
Используется для внутренних уведомлений: заявки, регистрации, конец триала.

Сообщение может быть размечено HTML (``parse_mode=HTML``) и нести кнопки-ссылки
(``inline_keyboard``) — так устроены карточки клиентов для команды. Всё
динамическое в таком тексте экранирует тот, кто его собирает
(:mod:`app.shared.notifications.team_format`).

Не путать с Telegram **Gateway** из OTP: тот шлёт код подтверждения на номер по
своему шаблону и произвольный текст не принимает.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Sequence
from typing import Any

import httpx
import structlog

from app.shared.notifications.channels import ChatButton, MessageDeliveryError

logger = structlog.get_logger(__name__)

_TAG = re.compile(r"<[^>]+>")


class _MessageRejectedError(MessageDeliveryError):
    """Bot API отклонил само сообщение (HTTP 400): разметку, кнопку, длину."""


def _plain(text: str) -> str:
    """HTML-текст → простой: теги долой, сущности (``&lt;``, ``&amp;``) обратно в символы."""
    return html_lib.unescape(_TAG.sub("", text))


def _description(response: httpx.Response) -> str:
    """Причина отказа из тела ответа Bot API (``description``), если она там есть."""
    try:
        return str(response.json().get("description", f"HTTP {response.status_code}"))
    except ValueError:
        return f"HTTP {response.status_code}"


class TelegramChatChannel:
    """Отправка текста в чат через ``sendMessage`` Bot API."""

    name = "telegram-chat"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout: float,
        api_url: str = "https://api.telegram.org",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._api_url = api_url
        self._client = client

    async def send(
        self, text: str, *, html: bool = False, buttons: Sequence[ChatButton] = ()
    ) -> None:
        """Отправить сообщение; разметку или кнопки отклонили — повторить простым текстом.

        HTTP 400 у Bot API — это претензия к самому сообщению (не разобралась
        разметка, не понравилась ссылка в кнопке), а не к доставке. Повтор той
        же задачей через минуту получил бы тот же отказ, и заявка осталась бы
        незамеченной из-за одного символа. Поэтому сразу вторая попытка — без
        разметки и без кнопок: карточка выглядит скромнее, но доходит.
        """
        try:
            await self._deliver(self._payload(text, html=html, buttons=buttons))
        except _MessageRejectedError as exc:
            if not (html or buttons):
                raise
            logger.warning("telegram_chat_markup_rejected", error=str(exc))
            await self._deliver(self._payload(_plain(text) if html else text))

    def _payload(
        self, text: str, *, html: bool = False, buttons: Sequence[ChatButton] = ()
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            # Превью ссылки из комментария заявки заслонило бы саму карточку.
            "link_preview_options": {"is_disabled": True},
        }
        if html:
            payload["parse_mode"] = "HTML"
        if buttons:
            # Все кнопки одним рядом: подписи короткие («WhatsApp», «Telegram»).
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "url": url} for label, url in buttons]]
            }
        return payload

    async def _deliver(self, payload: dict[str, Any]) -> None:
        url = f"{self._api_url.rstrip('/')}/bot{self._bot_token}/sendMessage"
        try:
            response = await self._post(url, payload)
        except httpx.HTTPError as exc:
            raise MessageDeliveryError(f"telegram-chat: сеть недоступна: {exc}") from exc

        if response.status_code == httpx.codes.BAD_REQUEST:
            raise _MessageRejectedError(f"telegram-chat: {_description(response)}")
        if response.status_code != httpx.codes.OK:
            raise MessageDeliveryError(f"telegram-chat: HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            raise MessageDeliveryError(f"telegram-chat: {body.get('description', 'not delivered')}")

    async def _post(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=payload, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, json=payload)
