"""Клиент платёжной страницы FreedomPay (``init_payment.php``).

Запрос — form-data с подписью, ответ — XML с адресом страницы оплаты и
своей подписью; её мы сверяем: подделанный ``pg_redirect_url`` увёл бы
клинику платить не туда.
"""

from __future__ import annotations

import secrets
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

from app.shared.freedompay.signature import sign, verify

logger = structlog.get_logger(__name__)

INIT_SCRIPT = "init_payment.php"
#: Таймаут запросов к провайдеру.
FREEDOMPAY_TIMEOUT = 20.0


class FreedomPayError(Exception):
    """Провайдер не принял запрос или ответил не так, как ждали."""


@dataclass(frozen=True, slots=True)
class InitPaymentRequest:
    order_id: str
    amount: str  # сомы с двумя знаками: "3000.00"
    currency: str
    description: str
    result_url: str
    success_url: str | None
    failure_url: str | None
    user_phone: str | None
    user_email: str | None
    language: str
    testing_mode: bool
    lifetime_seconds: int
    extra: dict[str, str]


@dataclass(frozen=True, slots=True)
class InitPaymentResult:
    payment_id: str
    redirect_url: str
    #: Разобранный ответ провайдера как есть — хранится у платежа.
    raw: dict[str, str] = field(default_factory=dict)


class FreedomPayClient(Protocol):
    async def init_payment(self, request: InitPaymentRequest) -> InitPaymentResult:
        """Зарегистрировать платёж и получить адрес страницы оплаты."""
        ...


def _salt() -> str:
    return secrets.token_hex(8)


def parse_xml(text: str) -> dict[str, str]:
    """Плоский XML ``<response>`` → словарь текстов узлов."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FreedomPayError(f"ответ не XML: {exc}") from exc
    return {child.tag: (child.text or "").strip() for child in root}


class FreedomPayHttpClient:
    def __init__(
        self,
        api_url: str,
        merchant_id: str,
        secret_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._merchant_id = merchant_id
        self._secret = secret_key
        self._client = client or httpx.AsyncClient(timeout=FREEDOMPAY_TIMEOUT)

    async def init_payment(self, request: InitPaymentRequest) -> InitPaymentResult:
        params: dict[str, Any] = {
            "pg_merchant_id": self._merchant_id,
            "pg_order_id": request.order_id,
            "pg_amount": request.amount,
            "pg_currency": request.currency,
            "pg_description": request.description,
            "pg_result_url": request.result_url,
            "pg_request_method": "POST",
            "pg_language": request.language,
            "pg_testing_mode": "1" if request.testing_mode else "0",
            "pg_lifetime": str(request.lifetime_seconds),
            "pg_salt": _salt(),
            **request.extra,
        }
        if request.success_url:
            params["pg_success_url"] = request.success_url
        if request.failure_url:
            params["pg_failure_url"] = request.failure_url
        if request.user_phone:
            params["pg_user_phone"] = request.user_phone
        if request.user_email:
            params["pg_user_contact_email"] = request.user_email
        params["pg_sig"] = sign(INIT_SCRIPT, params, self._secret)
        try:
            resp = await self._client.post(f"{self._api_url}/{INIT_SCRIPT}", data=params)
        except httpx.HTTPError as exc:
            raise FreedomPayError(f"сеть: {exc}") from exc
        if resp.status_code >= 400:
            logger.error(
                "freedompay_init_http_error", status=resp.status_code, body=resp.text[:400]
            )
            raise FreedomPayError(f"HTTP {resp.status_code}")
        data = parse_xml(resp.text)
        if data.get("pg_status") != "ok":
            logger.warning(
                "freedompay_init_rejected",
                code=data.get("pg_error_code"),
                description=data.get("pg_error_description"),
            )
            raise FreedomPayError(
                f"{data.get('pg_error_code') or 'error'}: "
                f"{data.get('pg_error_description') or 'провайдер отклонил запрос'}"
            )
        if not verify(INIT_SCRIPT, data, self._secret):
            logger.error("freedompay_init_bad_signature")
            raise FreedomPayError("подпись ответа не сошлась")
        payment_id = data.get("pg_payment_id") or ""
        redirect_url = data.get("pg_redirect_url") or ""
        if not payment_id or not redirect_url:
            raise FreedomPayError("в ответе нет pg_payment_id или pg_redirect_url")
        return InitPaymentResult(payment_id=payment_id, redirect_url=redirect_url, raw=data)
