"""Транспорт WhatsApp-бота: отправка произвольного текста на номер.

Перенесено из OTP-канала (исходно — из ``showgo_api``) и обобщено до текста:
логика запроса у кода подтверждения и у уведомления одна и та же, а вот текст
разный, и держать две копии обработки ответа — значит однажды починить только
одну из них.

API принимает номер **без ведущего плюса** и отдаёт результат полем ``success``:
HTTP 200 с ``success: false`` — это недоставка.

⚠️ В исходнике запрос шёл с ``verify=False`` (проверка TLS отключена). Сюда это
НЕ перенесено: с выключенной проверкой содержимое сообщения уходит тому, кто
перехватил соединение.
"""

from __future__ import annotations

import httpx

from app.shared.notifications.channels import MessageDeliveryError


class WhatsAppTextChannel:
    """Канал WhatsApp (бот-шлюз с ``accountId``)."""

    name = "whatsapp"

    def __init__(
        self,
        *,
        api_url: str,
        account_id: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
        self._account_id = account_id
        self._timeout = timeout
        self._client = client

    async def send_text(self, phone: str, text: str) -> None:
        payload = {"accountId": self._account_id, "to": phone.lstrip("+"), "message": text}

        try:
            response = await self._post(payload)
        except httpx.HTTPError as exc:
            raise MessageDeliveryError(f"whatsapp: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise MessageDeliveryError(f"whatsapp: HTTP {response.status_code}")

        body = response.json()
        if not body.get("success"):
            raise MessageDeliveryError(f"whatsapp: {body.get('message', 'not delivered')}")

    async def _post(self, payload: dict[str, str]) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self._client is not None:
            return await self._client.post(
                self._api_url, json=payload, headers=headers, timeout=self._timeout
            )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(self._api_url, json=payload, headers=headers)
