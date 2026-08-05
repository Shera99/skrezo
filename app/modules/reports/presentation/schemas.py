"""Схемы модуля отчётов (camelCase наружу).

Все суммы — целые сомы. ``dateFrom``/``dateTo`` — фактически применённые границы
периода в таймзоне клиники (``dateFrom = null`` для «за всё время»).
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field

from app.modules.reports.domain.models import (
    AppointmentsBlock,
    ClinicReportRow,
    ClinicsReport,
    DoctorReportRow,
    DoctorsReport,
    MethodRevenue,
    PatientsBlock,
    ReportSummary,
    RevenueBlock,
    RevenuePoint,
    RevenueSeries,
    ServiceReportRow,
    ServicesReport,
)
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import PaymentMethod


class MethodRevenueOut(CamelModel):
    """Выручка по способу оплаты."""

    method: PaymentMethod
    income_tiyin: int
    refund_tiyin: int
    net_tiyin: int

    @classmethod
    def from_domain(cls, m: MethodRevenue) -> MethodRevenueOut:
        return cls(
            method=m.method,
            income_tiyin=m.income_tiyin,
            refund_tiyin=m.refund_tiyin,
            net_tiyin=m.net_tiyin,
        )


class RevenueBlockOut(CamelModel):
    """Деньги за период (по дате платежа)."""

    income_tiyin: int = Field(description="Приход, сомы")
    refund_tiyin: int = Field(description="Возвраты, сомы")
    net_tiyin: int = Field(description="Нетто = приход − возвраты")
    by_method: list[MethodRevenueOut]

    @classmethod
    def from_domain(cls, r: RevenueBlock) -> RevenueBlockOut:
        return cls(
            income_tiyin=r.income_tiyin,
            refund_tiyin=r.refund_tiyin,
            net_tiyin=r.net_tiyin,
            by_method=[MethodRevenueOut.from_domain(m) for m in r.by_method],
        )


class AppointmentsBlockOut(CamelModel):
    """Приёмы периода по статусам (по дате приёма)."""

    total: int
    scheduled: int
    confirmed: int
    arrived: int
    in_progress: int
    completed: int
    cancelled: int
    no_show: int

    @classmethod
    def from_domain(cls, a: AppointmentsBlock) -> AppointmentsBlockOut:
        return cls(
            total=a.total,
            scheduled=a.scheduled,
            confirmed=a.confirmed,
            arrived=a.arrived,
            in_progress=a.in_progress,
            completed=a.completed,
            cancelled=a.cancelled,
            no_show=a.no_show,
        )


class PatientsBlockOut(CamelModel):
    """Пациенты периода: новые для клиники против повторных."""

    total: int
    new: int = Field(description="Первый приём в этой клинике попал в период")
    returning: int

    @classmethod
    def from_domain(cls, p: PatientsBlock) -> PatientsBlockOut:
        return cls(total=p.total, new=p.new, returning=p.returning)


class ReportSummaryResponse(CamelModel):
    """Сводка отчётов за период."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    date_from: date | None = Field(default=None, description="null — за всё время")
    date_to: date
    revenue: RevenueBlockOut
    appointments: AppointmentsBlockOut
    patients: PatientsBlockOut
    charged_tiyin: int = Field(description="Начислено по завершённым приёмам периода, сомы")
    debt_tiyin: int = Field(description="Остаток по завершённым приёмам периода, сомы")
    average_check_tiyin: int = Field(description="Начислено / число завершённых приёмов")

    @classmethod
    def from_summary(cls, s: ReportSummary) -> ReportSummaryResponse:
        return cls(
            timezone=s.timezone,
            date_from=s.date_from,
            date_to=s.date_to,
            revenue=RevenueBlockOut.from_domain(s.revenue),
            appointments=AppointmentsBlockOut.from_domain(s.appointments),
            patients=PatientsBlockOut.from_domain(s.patients),
            charged_tiyin=s.charged_tiyin,
            debt_tiyin=s.debt_tiyin,
            average_check_tiyin=s.average_check_tiyin,
        )


class RevenuePointOut(CamelModel):
    """Точка временного ряда выручки."""

    bucket: date = Field(description="День или первое число месяца")
    income_tiyin: int
    refund_tiyin: int
    net_tiyin: int

    @classmethod
    def from_domain(cls, p: RevenuePoint) -> RevenuePointOut:
        return cls(
            bucket=p.bucket,
            income_tiyin=p.income_tiyin,
            refund_tiyin=p.refund_tiyin,
            net_tiyin=p.net_tiyin,
        )


class RevenueSeriesResponse(CamelModel):
    """Динамика выручки."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    group_by: str = Field(description="day | month", examples=["day"])
    points: list[RevenuePointOut]

    @classmethod
    def from_series(cls, s: RevenueSeries) -> RevenueSeriesResponse:
        return cls(
            timezone=s.timezone,
            group_by=s.group_by,
            points=[RevenuePointOut.from_domain(p) for p in s.points],
        )


class DoctorReportItem(CamelModel):
    """Строка отчёта по врачу."""

    membership_id: uuid.UUID
    full_name: str = Field(examples=["Иванов Иван"])
    specialization_name: str | None = Field(default=None, examples=["Терапевт"])
    appointments: int
    completed: int
    cancelled: int
    charged_tiyin: int = Field(description="Начислено по завершённым, сомы")
    paid_tiyin: int = Field(description="Оплачено нетто по его приёмам, сомы")
    debt_tiyin: int = Field(description="Начислено − оплачено, сомы")
    average_check_tiyin: int

    @classmethod
    def from_domain(cls, d: DoctorReportRow) -> DoctorReportItem:
        return cls(
            membership_id=d.membership_id,
            full_name=d.full_name,
            specialization_name=d.specialization_name,
            appointments=d.appointments,
            completed=d.completed,
            cancelled=d.cancelled,
            charged_tiyin=d.charged_tiyin,
            paid_tiyin=d.paid_tiyin,
            debt_tiyin=d.debt_tiyin,
            average_check_tiyin=d.average_check_tiyin,
        )


class DoctorsReportResponse(CamelModel):
    """Отчёт по врачам (с правом view_own — одна строка, сам врач)."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    rows: list[DoctorReportItem]

    @classmethod
    def from_report(cls, r: DoctorsReport) -> DoctorsReportResponse:
        return cls(timezone=r.timezone, rows=[DoctorReportItem.from_domain(x) for x in r.rows])


class ServiceReportItem(CamelModel):
    """Строка отчёта по услуге."""

    service_id: uuid.UUID | None = Field(default=None, description="null — манипуляции без услуги")
    name: str = Field(examples=["Лечение кариеса"])
    count: int = Field(description="Число манипуляций")
    revenue_tiyin: int = Field(description="Сумма по манипуляциям, сомы")

    @classmethod
    def from_domain(cls, s: ServiceReportRow) -> ServiceReportItem:
        return cls(
            service_id=s.service_id, name=s.name, count=s.count, revenue_tiyin=s.revenue_tiyin
        )


class ServicesReportResponse(CamelModel):
    """Топ услуг по убыванию выручки."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    rows: list[ServiceReportItem]

    @classmethod
    def from_report(cls, r: ServicesReport) -> ServicesReportResponse:
        return cls(timezone=r.timezone, rows=[ServiceReportItem.from_domain(x) for x in r.rows])


class ClinicReportItem(CamelModel):
    """Клиника в платформенном своде."""

    clinic_id: uuid.UUID
    name: str
    city_name: str | None = None
    revenue_net_tiyin: int = Field(description="Нетто кассы за период, сомы")
    debt_tiyin: int = Field(description="Остаток по завершённым приёмам периода, сомы")
    appointments: int
    completed: int
    doctors: int = Field(description="Активных врачей сейчас")
    patients: int = Field(description="Уникальных пациентов за период")

    @classmethod
    def from_domain(cls, c: ClinicReportRow) -> ClinicReportItem:
        return cls(
            clinic_id=c.clinic_id,
            name=c.name,
            city_name=c.city_name,
            revenue_net_tiyin=c.revenue_net_tiyin,
            debt_tiyin=c.debt_tiyin,
            appointments=c.appointments,
            completed=c.completed,
            doctors=c.doctors,
            patients=c.patients,
        )


class ClinicsReportResponse(CamelModel):
    """Свод по всем клиникам платформы (экран выбора клиники)."""

    date_from: date | None = Field(default=None, description="null — за всё время")
    date_to: date
    rows: list[ClinicReportItem]
    total_revenue_net_tiyin: int
    total_debt_tiyin: int

    @classmethod
    def from_report(cls, r: ClinicsReport) -> ClinicsReportResponse:
        return cls(
            date_from=r.date_from,
            date_to=r.date_to,
            rows=[ClinicReportItem.from_domain(x) for x in r.rows],
            total_revenue_net_tiyin=r.total_revenue_net_tiyin,
            total_debt_tiyin=r.total_debt_tiyin,
        )
