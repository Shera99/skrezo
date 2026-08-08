"""Доставка кода по SMS через SMSPRO (nikita.kg) — перенесено из showgo_api.

Провайдер принимает XML и отвечает XML же. Успех — ``status`` равный ``0``
(принято) или ``11`` (принято частично): всё остальное считаем недоставкой.

XML собирается через ``ElementTree``, а не форматированием строки, как в
исходнике: логин, пароль и текст попадают внутрь документа, и любой символ
``&`` или ``<`` в них ломает запрос — молча, потому что провайдер ответит
разбором, а не ошибкой валидации.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import structlog

from app.core.config import settings
from app.modules.identity.domain.otp_message import sms_text
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError

logger = structlog.get_logger(__name__)

#: Коды ответа провайдера, означающие «сообщение принято».
_ACCEPTED_STATUSES = frozenset({"0", "11"})


def _build_request(phone: str, text: str, external_id: str) -> bytes:
    """Собрать XML-запрос провайдера (экранирование берёт на себя ElementTree)."""
    message = ET.Element("message")
    ET.SubElement(message, "login").text = settings.smspro_login
    ET.SubElement(message, "pwd").text = settings.smspro_password
    ET.SubElement(message, "id").text = external_id
    ET.SubElement(message, "sender").text = settings.smspro_sender
    ET.SubElement(message, "text").text = text
    phones = ET.SubElement(message, "phones")
    ET.SubElement(phones, "phone").text = phone
    return bytes(ET.tostring(message, encoding="UTF-8", xml_declaration=True))


class SmsProChannel:
    """Канал SMS через SMSPRO (nikita.kg)."""

    name = "sms"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, phone: str, code: str) -> None:
        # external_id провайдера — наш идентификатор отправки; берём код-независимое
        # значение, чтобы он не утёк в чужие логи.
        external_id = f"{abs(hash(phone)) % 10**9}"
        body = _build_request(phone, sms_text(code), external_id)

        try:
            response = await self._post(body)
        except httpx.HTTPError as exc:
            raise OtpDeliveryError(f"sms: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise OtpDeliveryError(f"sms: HTTP {response.status_code}")

        status, message = _parse_response(response.text)
        if status not in _ACCEPTED_STATUSES:
            raise OtpDeliveryError(f"sms: провайдер отклонил ({status}): {message}")

    async def _post(self, body: bytes) -> httpx.Response:
        timeout = settings.otp_delivery_timeout_seconds
        headers = {"Content-Type": "application/xml"}
        if self._client is not None:
            return await self._client.post(
                settings.smspro_url, content=body, headers=headers, timeout=timeout
            )
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(settings.smspro_url, content=body, headers=headers)


def _parse_response(payload: str) -> tuple[str, str]:
    """Вынуть ``status`` и ``message`` из XML-ответа; битый XML — недоставка."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise OtpDeliveryError(f"sms: не удалось разобрать ответ провайдера: {exc}") from exc
    status = (root.findtext("status") or "").strip()
    message = (root.findtext("message") or "").strip()
    return status, message
