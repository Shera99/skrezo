"""Схемы модуля отчётов (camelCase наружу).

Все суммы — целые сомы. ``dateFrom``/``dateTo`` — фактически применённые границы
периода в таймзоне клиники (``dateFrom = null`` для «за всё время»).
"""

from __future__ import annotations

import uuid
from datetime import date
from datetime import date as date_type

from pydantic import Field

from app.modules.reports.domain.models import (
    AppointmentsBlock,
    CancelReasonsBlock,
    ClinicReportRow,
    ClinicsReport,
    DayDashboard,
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

    income_tiyin: int = Field(description="Приход, тыйыны")
    refund_tiyin: int = Field(description="Возвраты, тыйыны")
    net_tiyin: int = Field(description="Нетто = приход − возвраты, тыйыны")
    refunds_count: int = Field(description="Число операций возврата за период", examples=[2])
    by_method: list[MethodRevenueOut]

    @classmethod
    def from_domain(cls, r: RevenueBlock) -> RevenueBlockOut:
        return cls(
            income_tiyin=r.income_tiyin,
            refund_tiyin=r.refund_tiyin,
            net_tiyin=r.net_tiyin,
            refunds_count=r.refunds_count,
            by_method=[MethodRevenueOut.from_domain(m) for m in r.by_method],
        )


class CancelReasonsOut(CamelModel):
    """Отмены периода по причинам из справочника ``CancelReason``."""

    patient_request: int = Field(description="Пациент попросил отменить")
    patient_illness: int = Field(description="Пациент заболел")
    clinic_reschedule: int = Field(description="Клиника перенесла")
    doctor_unavailable: int = Field(description="Врач недоступен")
    duplicate: int = Field(description="Дубль записи")
    other: int = Field(description="Другое (с комментарием)")

    @classmethod
    def from_domain(cls, c: CancelReasonsBlock) -> CancelReasonsOut:
        return cls(
            patient_request=c.patient_request,
            patient_illness=c.patient_illness,
            clinic_reschedule=c.clinic_reschedule,
            doctor_unavailable=c.doctor_unavailable,
            duplicate=c.duplicate,
            other=c.other,
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
    cancel_reasons: CancelReasonsOut = Field(
        description="Отмены по причинам справочника; отмены без причины в разбивку не входят"
    )

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
            cancel_reasons=CancelReasonsOut.from_domain(a.cancel_reasons),
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
    charged_tiyin: int = Field(
        description="Начислено по завершённым приёмам периода (уже со скидкой), тыйыны"
    )
    discounts_tiyin: int = Field(
        description="Сумма скидок по этим приёмам, тыйыны", examples=[42500]
    )
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
            discounts_tiyin=s.discounts_tiyin,
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
    no_show: int = Field(description="Неявки")
    charged_tiyin: int = Field(description="Начислено по завершённым, тыйыны")
    paid_tiyin: int = Field(description="Оплачено нетто по его приёмам, тыйыны")
    debt_tiyin: int = Field(description="Начислено − оплачено, тыйыны")
    average_check_tiyin: int
    booked_minutes: int = Field(
        description="Минуты приёмов периода без отменённых, неявок и черновиков",
        examples=[1350],
    )
    working_minutes: int | None = Field(
        default=None,
        description=(
            "Рабочие минуты по графику за период: часы клиники ∩ график врача − обед − "
            "отсутствия; null — у периода нет начала (period=all)"
        ),
        examples=[2400],
    )
    workload_percent: int | None = Field(
        default=None,
        description=(
            "Загрузка = bookedMinutes / workingMinutes × 100, округлено; null — рабочих "
            "минут нет. Больше 100 — приёмы шли вне графика"
        ),
        examples=[56],
    )

    @classmethod
    def from_domain(cls, d: DoctorReportRow) -> DoctorReportItem:
        return cls(
            membership_id=d.membership_id,
            full_name=d.full_name,
            specialization_name=d.specialization_name,
            appointments=d.appointments,
            completed=d.completed,
            cancelled=d.cancelled,
            no_show=d.no_show,
            charged_tiyin=d.charged_tiyin,
            paid_tiyin=d.paid_tiyin,
            debt_tiyin=d.debt_tiyin,
            average_check_tiyin=d.average_check_tiyin,
            booked_minutes=d.booked_minutes,
            working_minutes=d.working_minutes,
            workload_percent=d.workload_percent,
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
    count: int = Field(description="Сколько раз услуга оказана (с учётом количества)")
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


class DayDashboardResponse(CamelModel):
    """Сводка текущего дня клиники — утренний экран одним запросом."""

    # Аннотация через псевдоним: имя поля ``date`` иначе перекрывает тип ``date``,
    # и pydantic не может разрешить аннотацию.
    date: date_type = Field(description="Сегодняшняя дата В ТАЙМЗОНЕ КЛИНИКИ, а не браузера")
    timezone: str = Field(examples=["Asia/Bishkek"])
    appointments: AppointmentsBlockOut = Field(description="Приёмы на сегодня по статусам")
    revenue_tiyin: int = Field(
        description="Нетто кассы за сегодня: платежи минус возвраты, тыйыны", examples=[125000]
    )
    outstanding_tiyin: int = Field(
        description=(
            "Непогашенный долг клиники за ВСЁ время (начислено минус оплачено). "
            "Не путать с `debtTiyin` в отчётах — там долг за выбранный период"
        ),
        examples=[430000],
    )

    @classmethod
    def from_domain(cls, d: DayDashboard) -> DayDashboardResponse:
        return cls(
            date=d.date,
            timezone=d.timezone,
            appointments=AppointmentsBlockOut.from_domain(d.appointments),
            revenue_tiyin=d.revenue_tiyin,
            outstanding_tiyin=d.outstanding_tiyin,
        )
