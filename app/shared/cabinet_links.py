"""Ссылки на веб-кабинет клиники — единственное место, где собираются адреса.

Кабинет живёт на ``CABINET_URL`` (прод — https://office.skrezo.com). Адреса
здесь — контракт с фронтом и приложением: домен привязан к iOS-приложению
(universal links), поэтому ссылка обязана вести прямо на него — без
``http://``, ``www`` и коротких ссылок-редиректов, иначе iOS откроет браузер
вместо приложения.

``/pay`` — исключение по замыслу: этот путь всегда открывается в браузере, а не
в приложении (оплаты в приложении нет по правилам App Store и Google Play).
Туда ведут и призыв «оплатить или продлить», и возврат со страницы оплаты —
человек возвращается туда же, где платил, и видит итог платежа.

Пустой ``CABINET_URL`` (локальная разработка) — ссылок нет: функции отдают
``None``, а тексты сообщений обходятся без ссылки.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode, urlsplit

from app.core.config import settings


def cabinet_url(path: str, **query: str) -> str | None:
    """Адрес страницы кабинета; ``None``, если адрес кабинета не настроен."""
    base = settings.cabinet_url
    if not base:
        return None
    url = f"{base}/{path.lstrip('/')}"
    return f"{url}?{urlencode(query)}" if query else url


def pay_url(payment_id: uuid.UUID | None = None) -> str | None:
    """Страница оплаты: без id — «оплатить или продлить», с id — итог платежа."""
    if payment_id is None:
        return cabinet_url("pay")
    return cabinet_url("pay", payment=str(payment_id))


def cabinet_host() -> str | None:
    """Домен кабинета без схемы — для привязки кода из SMS к сайту и приложению."""
    if not settings.cabinet_url:
        return None
    return urlsplit(settings.cabinet_url).hostname
