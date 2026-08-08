"""Транспорт SMS через SMSPRO (nikita.kg): отправка произвольного текста.

Перенесено из OTP-канала и обобщено до текста — по той же причине, что и
WhatsApp: разбор ответа провайдера должен жить в одном месте.

Провайдер принимает XML и отвечает XML же. Успех — ``status`` равный ``0``
(принято) или ``11`` (принято частично): всё остальное считаем недоставкой.

XML собирается через ``ElementTree``, а не форматированием строки, как в
исходнике: логин, пароль и текст попадают внутрь документа, и любой символ
``&`` или ``<`` в них ломает запрос — молча.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from app.shared.notifications.channels import MessageDeliveryError

#: Коды ответа провайдера, означающие «сообщение принято».
_ACCEPTED_STATUSES = frozenset({"0", "11"})


class SmsProTextChannel:
    """Канал SMS через SMSPRO (nikita.kg)."""

    name = "sms"

    def __init__(
        self,
        *,
        url: str,
        login: str,
        password: str,
        sender: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._login = login
        self._password = password
        self._sender = sender
        self._timeout = timeout
        self._client = client

    async def send_text(self, phone: str, text: str) -> None:
        # external_id провайдера — наш идентификатор отправки; берём независимое
        # от текста значение, чтобы содержимое не утекло в чужие логи.
        external_id = f"{abs(hash(phone)) % 10**9}"
        body = self._build_request(phone, text, external_id)

        try:
            response = await self._post(body)
        except httpx.HTTPError as exc:
            raise MessageDeliveryError(f"sms: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise MessageDeliveryError(f"sms: HTTP {response.status_code}")

        status, message = _parse_response(response.text)
        if status not in _ACCEPTED_STATUSES:
            raise MessageDeliveryError(f"sms: провайдер отклонил ({status}): {message}")

    def _build_request(self, phone: str, text: str, external_id: str) -> bytes:
        """Собрать XML-запрос провайдера (экранирование берёт на себя ElementTree)."""
        message = ET.Element("message")
        ET.SubElement(message, "login").text = self._login
        ET.SubElement(message, "pwd").text = self._password
        ET.SubElement(message, "id").text = external_id
        ET.SubElement(message, "sender").text = self._sender
        ET.SubElement(message, "text").text = text
        phones = ET.SubElement(message, "phones")
        ET.SubElement(phones, "phone").text = phone
        return bytes(ET.tostring(message, encoding="UTF-8", xml_declaration=True))

    async def _post(self, body: bytes) -> httpx.Response:
        headers = {"Content-Type": "application/xml"}
        if self._client is not None:
            return await self._client.post(
                self._url, content=body, headers=headers, timeout=self._timeout
            )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(self._url, content=body, headers=headers)


def _parse_response(payload: str) -> tuple[str, str]:
    """Вынуть ``status`` и ``message`` из XML-ответа; битый XML — недоставка."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MessageDeliveryError(f"sms: не удалось разобрать ответ провайдера: {exc}") from exc
    status = (root.findtext("status") or "").strip()
    message = (root.findtext("message") or "").strip()
    return status, message
