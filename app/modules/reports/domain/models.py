"""Доменные структуры отчётов.

Деньги — целые сомы. Разделение периодов: **деньги считаются по дате платежа**
(``cash_payment.created_at``), **приёмы/услуги/врачи — по дате приёма**
(``appointment.scheduled_at``), иначе вчерашний приём, оплаченный сегодня, попал
бы не в тот период.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from app.shared.db.enums import PaymentMethod


@dataclass(frozen=True, slots=True)
class MethodRevenue:
    """Выручка по способу оплаты."""

    method: PaymentMethod
    income: int
    refund: int
    net: int


@dataclass(frozen=True, slots=True)
class RevenueBlock:
    """Итог по деньгам за период (по дате платежа)."""

    income: int
    refund: int
    net: int
    by_method: list[MethodRevenue]


@dataclass(frozen=True, slots=True)
class AppointmentsBlock:
    """Приёмы периода по статусам (по дате приёма)."""

    total: int
    scheduled: int
    confirmed: int
    arrived: int
    in_progress: int
    completed: int
    cancelled: int
    no_show: int


@dataclass(frozen=True, slots=True)
class PatientsBlock:
    """Пациенты периода: новые для клиники против повторных."""

    total: int
    new: int
    returning: int


@dataclass(frozen=True, slots=True)
class ReportSummary:
    """Всё для экрана отчётов одним запросом."""

    timezone: str
    date_from: date | None
    date_to: date
    revenue: RevenueBlock
    appointments: AppointmentsBlock
    patients: PatientsBlock
    charged: int  # начислено по завершённым приёмам периода
    debt: int  # остаток по завершённым приёмам периода
    average_check: int  # начислено / число завершённых приёмов


@dataclass(frozen=True, slots=True)
class RevenuePoint:
    """Точка временного ряда выручки."""

    bucket: date
    income: int
    refund: int
    net: int


@dataclass(frozen=True, slots=True)
class RevenueSeries:
    """Динамика выручки по дням или месяцам."""

    timezone: str
    group_by: str
    points: list[RevenuePoint]


@dataclass(frozen=True, slots=True)
class DoctorReportRow:
    """Строка отчёта по врачу."""

    membership_id: uuid.UUID
    full_name: str
    specialization_name: str | None
    appointments: int
    completed: int
    cancelled: int
    charged: int  # начислено по завершённым
    paid: int  # оплачено нетто по его приёмам
    debt: int  # начислено − оплачено
    average_check: int


@dataclass(frozen=True, slots=True)
class DoctorsReport:
    """Отчёт по врачам за период."""

    timezone: str
    rows: list[DoctorReportRow]


@dataclass(frozen=True, slots=True)
class ServiceReportRow:
    """Строка отчёта по услуге."""

    service_id: uuid.UUID | None
    name: str
    count: int
    revenue: int


@dataclass(frozen=True, slots=True)
class ServicesReport:
    """Топ услуг за период (по манипуляциям в приёмах)."""

    timezone: str
    rows: list[ServiceReportRow]


@dataclass(frozen=True, slots=True)
class ClinicReportRow:
    """Строка платформенного свода: клиника и её ключевые метрики."""

    clinic_id: uuid.UUID
    name: str
    city_name: str | None
    revenue_net: int
    debt: int
    appointments: int
    completed: int
    doctors: int
    patients: int


@dataclass(frozen=True, slots=True)
class ClinicsReport:
    """Свод по всем клиникам платформы (экран выбора клиники)."""

    date_from: date | None
    date_to: date
    rows: list[ClinicReportRow]
    total_revenue_net: int
    total_debt: int
