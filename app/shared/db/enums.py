"""Перечисления и типы БД, общие для всех модулей.

Объекты ``*Type`` — единые экземпляры SQLAlchemy-``Enum``: соответствующий
PG-тип создаётся один раз и переиспользуется всеми моделями.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class Lang(enum.StrEnum):
    """Поддерживаемые языки интерфейса и контента."""

    KG = "kg"  # кыргызский
    KZ = "kz"  # казахский
    RU = "ru"  # русский
    EN = "en"  # английский


class ClinicType(enum.StrEnum):
    """Тип аккаунта клиники."""

    SOLO = "solo"
    CLINIC = "clinic"


class SubscriptionStatus(enum.StrEnum):
    """Статус подписки клиники на Skrezo."""

    TRIAL = "trial"  # 30 дней бесплатно
    AWAITING_CARD = "awaiting_card"  # карта не привязана за 7 дней — доступ закрыт
    ACTIVE = "active"
    PAST_DUE = "past_due"  # списание не прошло — доступ закрыт
    BLOCKED = "blocked"  # ручной блок Skrezo (не из-за оплаты)
    CANCELLED = "cancelled"  # клиент ушёл, архив


DEFAULT_LANG = Lang.RU

# Единые типы для колонок (PG enum один на всю схему).
LangType = SAEnum(Lang, name="lang_enum", values_callable=lambda e: [m.value for m in e])
ClinicTypeType = SAEnum(
    ClinicType, name="clinic_type", values_callable=lambda e: [m.value for m in e]
)
SubscriptionStatusType = SAEnum(
    SubscriptionStatus, name="subscription_status", values_callable=lambda e: [m.value for m in e]
)
