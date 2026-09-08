"""Сборка клиента FreedomPay из настроек; без реквизитов оплата недоступна."""

from __future__ import annotations

from app.core.config import settings
from app.shared.freedompay.client import FREEDOMPAY_TIMEOUT, FreedomPayClient, FreedomPayHttpClient
from app.shared.http import shared_async_client


def build_freedompay_client() -> FreedomPayClient | None:
    """Клиент при заполненных ``FREEDOMPAY_MERCHANT_ID``/``SECRET_KEY``, иначе ``None``.

    Лог-заглушки нет намеренно: «оплата прошла» в логе вместо денег —
    не безвредно. Нет реквизитов — ручка оплаты отвечает 409.
    """
    if settings.freedompay_merchant_id and settings.freedompay_secret_key:
        return FreedomPayHttpClient(
            settings.freedompay_api_url,
            settings.freedompay_merchant_id,
            settings.freedompay_secret_key,
            client=shared_async_client("freedompay", timeout=FREEDOMPAY_TIMEOUT),
        )
    return None
