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


def patient_url(patient_id: uuid.UUID) -> str | None:
    """Карточка пациента в кабинете."""
    return cabinet_url(f"patients/{patient_id}")


def chat_url(conversation_id: uuid.UUID) -> str | None:
    """Переписка с пациентом (диалог WhatsApp)."""
    return cabinet_url(f"chat/{conversation_id}")


def assistant_learn_url(item_id: uuid.UUID) -> str | None:
    """Обучение ассистента: карточка пробела знаний или предложения."""
    return cabinet_url(f"assistant/learn/{item_id}")


def schedule_url() -> str | None:
    """Расписание клиники."""
    return cabinet_url("schedule")


def event_url(
    event_type: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    *,
    patient_id: uuid.UUID | None = None,
) -> str | None:
    """Раздел кабинета для события каталога уведомлений; ``None`` — раздела нет.

    Правило одно на все ленты: диалог → переписка, приём и сводка дня →
    расписание, платёж → карточка пациента (когда пациент известен), подписка →
    оплата. У событий о сотрудниках и ролях раздела нет.
    """
    if event_type.startswith("subscription."):
        return pay_url()
    if entity_type == "conversation" and entity_id is not None:
        return chat_url(entity_id)
    if entity_type == "appointment" or event_type.startswith("digest."):
        return schedule_url()
    if entity_type == "payment" and patient_id is not None:
        return patient_url(patient_id)
    return None


def cabinet_host() -> str | None:
    """Домен кабинета без схемы — для привязки кода из SMS к сайту и приложению."""
    if not settings.cabinet_url:
        return None
    return urlsplit(settings.cabinet_url).hostname
