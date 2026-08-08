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
    TRIAL_EXPIRED = "trial_expired"  # триал кончился, оплаты не было — только чтение
    AWAITING_CARD = "awaiting_card"  # карта не привязана за 7 дней — доступ закрыт
    ACTIVE = "active"
    PAST_DUE = "past_due"  # списание не прошло — доступ закрыт
    BLOCKED = "blocked"  # ручной блок Skrezo (не из-за оплаты)
    CANCELLED = "cancelled"  # клиент ушёл, архив


class ToothStatus(enum.StrEnum):
    """Текущее состояние зуба в одонтограмме пациента."""

    HEALTHY = "healthy"
    CARIES = "caries"
    FILLING = "filling"
    CROWN = "crown"
    IMPLANT = "implant"
    MISSING = "missing"
    BROKEN = "broken"
    ROOT = "root"
    UNDER_TREATMENT = "under_treatment"


class AppointmentType(enum.StrEnum):
    """Тип приёма."""

    CONSULTATION = "consultation"
    TREATMENT = "treatment"
    OPERATION = "operation"
    CLEANING = "cleaning"
    XRAY = "xray"


class AppointmentStatus(enum.StrEnum):
    """Статус приёма."""

    # Пациент заведён в клинику, но дату и услугу ещё не выбрал. Единственный
    # статус, при котором ``scheduled_at`` пуст (гарантирует CHECK в БД): такой
    # приём не занимает слот врача и не попадает в ленту дня и отчёты периода.
    DRAFT = "draft"
    SCHEDULED = "scheduled"  # запись создана, пациент ещё не подтвердил
    CONFIRMED = "confirmed"  # пациент подтвердил (напр. ответом в WhatsApp)
    ARRIVED = "arrived"  # пришёл в клинику, ждёт врача
    IN_PROGRESS = "in_progress"  # идёт приём (пациент в кресле)
    COMPLETED = "completed"  # завершён (и оплачен, если платёж проведён)
    CANCELLED = "cancelled"  # отменён; причина — в ``cancel_reason``
    NO_SHOW = "no_show"  # не пришёл


# Статусы «приём в работе»: между ними можно двигаться свободно, они занимают
# слот врача и участвуют в проверке пересечений.
ACTIVE_APPOINTMENT_STATUSES: frozenset[AppointmentStatus] = frozenset(
    {
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.ARRIVED,
        AppointmentStatus.IN_PROGRESS,
    }
)


class AppointmentItemStatus(enum.StrEnum):
    """Статус позиции приёма (услуга с количеством, возможно — по зубам)."""

    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaymentMethod(enum.StrEnum):
    """Способ оплаты в кассе клиники."""

    CASH = "cash"  # наличные
    CARD = "card"  # карта
    TRANSFER = "transfer"  # перевод/безнал


class PaymentKind(enum.StrEnum):
    """Направление кассовой операции: приход денег или возврат."""

    PAYMENT = "payment"  # платёж (приход)
    REFUND = "refund"  # возврат (расход)


class CancelReason(enum.StrEnum):
    """Причина отмены приёма — справочник, чтобы отмены можно было считать."""

    PATIENT_REQUEST = "patient_request"  # пациент попросил отменить
    PATIENT_ILLNESS = "patient_illness"  # пациент заболел
    CLINIC_RESCHEDULE = "clinic_reschedule"  # клиника перенесла
    DOCTOR_UNAVAILABLE = "doctor_unavailable"  # врач недоступен
    DUPLICATE = "duplicate"  # дубль записи
    OTHER = "other"  # иное — обязателен комментарий


class DoctorsCountRange(enum.StrEnum):
    """Размер клиники в заявке — диапазон числа врачей."""

    FROM_2_TO_5 = "2-5"
    FROM_6_TO_10 = "6-10"
    OVER_10 = "10+"


class ClinicApplicationStatus(enum.StrEnum):
    """Статус обработки заявки клиники в админке Skrezo."""

    PENDING = "pending"  # на рассмотрении (стартовый)
    REVIEWED = "reviewed"  # рассмотрен
    REJECTED = "rejected"  # отклонён


DEFAULT_LANG = Lang.RU

# Единые типы для колонок (PG enum один на всю схему).
LangType = SAEnum(Lang, name="lang_enum", values_callable=lambda e: [m.value for m in e])
ClinicTypeType = SAEnum(
    ClinicType, name="clinic_type", values_callable=lambda e: [m.value for m in e]
)
SubscriptionStatusType = SAEnum(
    SubscriptionStatus, name="subscription_status", values_callable=lambda e: [m.value for m in e]
)
ToothStatusType = SAEnum(
    ToothStatus, name="tooth_status", values_callable=lambda e: [m.value for m in e]
)
AppointmentTypeType = SAEnum(
    AppointmentType, name="appointment_type", values_callable=lambda e: [m.value for m in e]
)
AppointmentStatusType = SAEnum(
    AppointmentStatus, name="appointment_status", values_callable=lambda e: [m.value for m in e]
)
AppointmentItemStatusType = SAEnum(
    AppointmentItemStatus,
    name="appointment_item_status",
    values_callable=lambda e: [m.value for m in e],
)
PaymentMethodType = SAEnum(
    PaymentMethod, name="payment_method", values_callable=lambda e: [m.value for m in e]
)
PaymentKindType = SAEnum(
    PaymentKind, name="payment_kind", values_callable=lambda e: [m.value for m in e]
)
CancelReasonType = SAEnum(
    CancelReason, name="cancel_reason", values_callable=lambda e: [m.value for m in e]
)
DoctorsCountRangeType = SAEnum(
    DoctorsCountRange, name="doctors_count_range", values_callable=lambda e: [m.value for m in e]
)
ClinicApplicationStatusType = SAEnum(
    ClinicApplicationStatus,
    name="clinic_application_status",
    values_callable=lambda e: [m.value for m in e],
)
