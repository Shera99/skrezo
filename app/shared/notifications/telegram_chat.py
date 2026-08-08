"""Транспорт «сообщение в Telegram-чат» через Bot API.

Стоит особняком от каналов на телефон: адресат тут не номер, а чат (общий чат
команды Skrezo), поэтому и контракт другой — ``send(text)`` без телефона.
Используется для внутренних уведомлений: новый лид, новая заявка клиники.

Не путать с Telegram **Gateway** из OTP: тот шлёт код подтверждения на номер по
своему шаблону и произвольный текст не принимает.
"""

from __future__ import annotations

import httpx

from app.shared.notifications.channels import MessageDeliveryError


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

    async def send(self, text: str) -> None:
        url = f"{self._api_url.rstrip('/')}/bot{self._bot_token}/sendMessage"
        # HTML-разметку намеренно не включаем: в тексте бывают названия клиник с
        # «<» и «&», и без экранирования Bot API отклонил бы такое сообщение.
        payload = {"chat_id": self._chat_id, "text": text}

        try:
            response = await self._post(url, payload)
        except httpx.HTTPError as exc:
            raise MessageDeliveryError(f"telegram-chat: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise MessageDeliveryError(f"telegram-chat: HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            raise MessageDeliveryError(f"telegram-chat: {body.get('description', 'not delivered')}")

    async def _post(self, url: str, payload: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=payload, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, json=payload)
