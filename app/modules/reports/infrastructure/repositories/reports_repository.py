"""Репозиторий отчётов: агрегаты по деньгам, приёмам, врачам, услугам, клиникам.

Разделение периодов: деньги фильтруются по ``cash_payment.created_at``, приёмы и
всё производное от них — по ``appointment.scheduled_at``.

``own_membership_id`` (право ``reports.view_own``) сужает выборку до приёмов, где
вызывающий — лечащий врач; для денег фильтр применяется через join к приёму.
Индексы под это уже есть: ``ix_appointment_clinic_scheduled_at`` и
``ix_cash_payment_clinic_created``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.reports.domain.models import (
    AppointmentsBlock,
    CancelReasonsBlock,
    ClinicReportRow,
    DoctorReportRow,
    MethodRevenue,
    RevenueBlock,
    RevenuePoint,
    ServiceReportRow,
)
from app.shared.db.enums import AppointmentStatus, CancelReason, Lang, PaymentKind
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_item import AppointmentItem
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.service import Service
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation

_NO_SERVICE = "Без услуги"


# Статусы, при которых приём занимает время врача, — числитель загрузки.
# Отменённые и неявки слот не «загружают», у черновиков нет даты.
_BOOKED_STATUSES = (
    AppointmentStatus.SCHEDULED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.ARRIVED,
    AppointmentStatus.IN_PROGRESS,
    AppointmentStatus.COMPLETED,
)


def _income() -> ColumnElement[int]:
    return func.coalesce(
        func.sum(
            case((CashPayment.kind == PaymentKind.PAYMENT, CashPayment.amount_tiyin), else_=0)
        ),
        0,
    )


def _refund() -> ColumnElement[int]:
    return func.coalesce(
        func.sum(case((CashPayment.kind == PaymentKind.REFUND, CashPayment.amount_tiyin), else_=0)),
        0,
    )


def _refunds_count() -> ColumnElement[int]:
    """Число операций возврата — сколько раз возвращали, а не на сколько."""
    return func.coalesce(
        func.sum(case((CashPayment.kind == PaymentKind.REFUND, 1), else_=0)),
        0,
    )


def _signed_amount() -> ColumnElement[int]:
    """Знаковая сумма операции: платёж прибавляет, возврат вычитает."""
    return case(
        (CashPayment.kind == PaymentKind.PAYMENT, CashPayment.amount_tiyin),
        else_=-CashPayment.amount_tiyin,
    )


class ReportsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Общие фильтры ──────────────────────────────────────────────────────
    @staticmethod
    def _appointment_filters(
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime | None,
        own_membership_id: uuid.UUID | None,
    ) -> list[ColumnElement[bool]]:
        """Фильтры приёмов; ``None`` в границе означает «без неё».

        Верхняя граница необязательна ради «долга за всё время»: приём,
        назначенный на сегодня, но позже текущего часа, — такой же долг, как
        вчерашний, и обрезать его по «сейчас» значит занижать сумму.
        """
        conditions: list[ColumnElement[bool]] = [Appointment.clinic_id == clinic_id]
        if end is not None:
            conditions.append(Appointment.scheduled_at < end)
        if start is not None:
            conditions.append(Appointment.scheduled_at >= start)
        if own_membership_id is not None:
            conditions.append(Appointment.doctor_membership_id == own_membership_id)
        return conditions

    @staticmethod
    def _payment_filters(
        clinic_id: uuid.UUID, start: datetime | None, end: datetime
    ) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [
            CashPayment.clinic_id == clinic_id,
            CashPayment.created_at < end,
        ]
        if start is not None:
            conditions.append(CashPayment.created_at >= start)
        return conditions

    def _payments_query(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
    ) -> Select[Any]:
        """Заготовка выборки по кассе; own-скоуп — через join к приёму.

        ``select_from(CashPayment)`` обязателен: без явной левой сущности пустой
        ``select()`` не может присоединить приём (SQLAlchemy не выводит FROM).
        """
        stmt = (
            select().select_from(CashPayment).where(*self._payment_filters(clinic_id, start, end))
        )
        if own_membership_id is not None:
            stmt = stmt.join(Appointment, Appointment.id == CashPayment.appointment_id).where(
                Appointment.doctor_membership_id == own_membership_id
            )
        return stmt

    # ── Деньги ─────────────────────────────────────────────────────────────
    async def revenue(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
    ) -> RevenueBlock:
        """Приход/возврат/нетто за период, всего и по способам оплаты, и число возвратов."""
        stmt = (
            self._payments_query(clinic_id, start, end, own_membership_id)
            .with_only_columns(CashPayment.method, _income(), _refund(), _refunds_count())
            .group_by(CashPayment.method)
        )
        rows = (await self._session.execute(stmt)).all()
        by_method = [
            MethodRevenue(
                method=r[0],
                income_tiyin=int(r[1]),
                refund_tiyin=int(r[2]),
                net_tiyin=int(r[1]) - int(r[2]),
            )
            for r in rows
        ]
        income = sum(m.income_tiyin for m in by_method)
        refund = sum(m.refund_tiyin for m in by_method)
        return RevenueBlock(
            income_tiyin=income,
            refund_tiyin=refund,
            net_tiyin=income - refund,
            refunds_count=sum(int(r[3]) for r in rows),
            by_method=by_method,
        )

    async def revenue_series(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
        *,
        group_by: str,
        timezone: str,
    ) -> list[RevenuePoint]:
        """Динамика выручки по дням/месяцам в таймзоне клиники."""
        # created_at (timestamptz) → локальное время клиники → усечение до дня/месяца.
        local = func.timezone(timezone, CashPayment.created_at)
        bucket = func.date_trunc(literal(group_by), local).label("bucket")
        stmt = (
            self._payments_query(clinic_id, start, end, own_membership_id)
            .with_only_columns(bucket, _income(), _refund())
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            RevenuePoint(
                bucket=r[0].date() if isinstance(r[0], datetime) else r[0],
                income_tiyin=int(r[1]),
                refund_tiyin=int(r[2]),
                net_tiyin=int(r[1]) - int(r[2]),
            )
            for r in rows
        ]

    # ── Приёмы ─────────────────────────────────────────────────────────────
    async def appointments_by_status(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
    ) -> AppointmentsBlock:
        """Число приёмов периода по статусам, отмены — ещё и по причинам.

        Один запрос с группировкой по статусу и причине: причина заполнена
        только у отменённых, у остальных она ``NULL`` и группу не дробит.
        """
        rows = (
            await self._session.execute(
                select(Appointment.status, Appointment.cancel_reason, func.count())
                .where(*self._appointment_filters(clinic_id, start, end, own_membership_id))
                .group_by(Appointment.status, Appointment.cancel_reason)
            )
        ).all()
        by_status: dict[AppointmentStatus, int] = {}
        by_reason: dict[CancelReason, int] = {}
        for status, reason, count in rows:
            by_status[status] = by_status.get(status, 0) + int(count)
            if status == AppointmentStatus.CANCELLED and reason is not None:
                by_reason[reason] = by_reason.get(reason, 0) + int(count)
        return AppointmentsBlock(
            total=sum(by_status.values()),
            scheduled=by_status.get(AppointmentStatus.SCHEDULED, 0),
            confirmed=by_status.get(AppointmentStatus.CONFIRMED, 0),
            arrived=by_status.get(AppointmentStatus.ARRIVED, 0),
            in_progress=by_status.get(AppointmentStatus.IN_PROGRESS, 0),
            completed=by_status.get(AppointmentStatus.COMPLETED, 0),
            cancelled=by_status.get(AppointmentStatus.CANCELLED, 0),
            no_show=by_status.get(AppointmentStatus.NO_SHOW, 0),
            cancel_reasons=CancelReasonsBlock(
                patient_request=by_reason.get(CancelReason.PATIENT_REQUEST, 0),
                patient_illness=by_reason.get(CancelReason.PATIENT_ILLNESS, 0),
                clinic_reschedule=by_reason.get(CancelReason.CLINIC_RESCHEDULE, 0),
                doctor_unavailable=by_reason.get(CancelReason.DOCTOR_UNAVAILABLE, 0),
                duplicate=by_reason.get(CancelReason.DUPLICATE, 0),
                other=by_reason.get(CancelReason.OTHER, 0),
            ),
        )

    async def charged_and_paid(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime | None,
        own_membership_id: uuid.UUID | None,
    ) -> tuple[int, int, int, int]:
        """Начислено, оплачено, число завершённых приёмов периода и сумма скидок.

        Оплата берётся по всем платежам этих приёмов независимо от даты платежа —
        иначе «начислено − оплачено» не сходилось бы с реальным долгом приёма.
        Скидки видны отдельной строкой: это единственная операция, где выручку
        уменьшает сотрудник, и владелец должен видеть её объём.
        """
        conditions = [
            *self._appointment_filters(clinic_id, start, end, own_membership_id),
            Appointment.status == AppointmentStatus.COMPLETED,
        ]
        charged_row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(Appointment.price_total_tiyin), 0),
                    func.count(),
                    func.coalesce(func.sum(Appointment.discount_tiyin), 0),
                ).where(*conditions)
            )
        ).first()
        charged = int(charged_row[0]) if charged_row else 0
        completed = int(charged_row[1]) if charged_row else 0
        discounts = int(charged_row[2]) if charged_row else 0

        completed_ids = select(Appointment.id).where(*conditions)
        paid = (
            await self._session.execute(
                select(func.coalesce(func.sum(_signed_amount()), 0)).where(
                    CashPayment.appointment_id.in_(completed_ids)
                )
            )
        ).scalar_one()
        return charged, int(paid), completed, discounts

    # ── Пациенты ───────────────────────────────────────────────────────────
    async def patients(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
    ) -> tuple[int, int]:
        """(всего пациентов периода, из них новых). Новый = первый приём попал в период."""
        total = (
            await self._session.execute(
                select(func.count(func.distinct(Appointment.patient_id))).where(
                    *self._appointment_filters(clinic_id, start, end, own_membership_id)
                )
            )
        ).scalar_one()

        # Первый визит пациента в эту клинику (в own-режиме — к этому врачу).
        base: list[ColumnElement[bool]] = [Appointment.clinic_id == clinic_id]
        if own_membership_id is not None:
            base.append(Appointment.doctor_membership_id == own_membership_id)
        first_visit = (
            select(
                Appointment.patient_id.label("patient_id"),
                func.min(Appointment.scheduled_at).label("first_at"),
            )
            .where(*base)
            .group_by(Appointment.patient_id)
            .subquery()
        )
        new_conditions: list[ColumnElement[bool]] = [first_visit.c.first_at < end]
        if start is not None:
            new_conditions.append(first_visit.c.first_at >= start)
        new = (
            await self._session.execute(
                select(func.count()).select_from(first_visit).where(*new_conditions)
            )
        ).scalar_one()
        return int(total), int(new)

    # ── Врачи ──────────────────────────────────────────────────────────────
    async def doctors(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
        *,
        lang: Lang,
    ) -> list[DoctorReportRow]:
        """Показатели по каждому врачу за период: приёмы, неявки, деньги, долг.

        Здесь же считаются минуты приёмов — числитель загрузки. Рабочие минуты
        по графику знает расписание, поэтому строка уходит с
        ``working_minutes=None``: их дописывает прикладной слой.
        """
        paid_sq = (
            select(
                Appointment.doctor_membership_id.label("membership_id"),
                func.coalesce(func.sum(_signed_amount()), 0).label("paid"),
            )
            .select_from(CashPayment)
            .join(Appointment, Appointment.id == CashPayment.appointment_id)
            .where(
                *self._appointment_filters(clinic_id, start, end, own_membership_id),
                Appointment.status == AppointmentStatus.COMPLETED,
            )
            .group_by(Appointment.doctor_membership_id)
            .subquery()
        )
        completed = case((Appointment.status == AppointmentStatus.COMPLETED, 1), else_=0)
        cancelled = case((Appointment.status == AppointmentStatus.CANCELLED, 1), else_=0)
        no_show = case((Appointment.status == AppointmentStatus.NO_SHOW, 1), else_=0)
        booked = case((Appointment.status.in_(_BOOKED_STATUSES), Appointment.duration_min), else_=0)
        charged = case(
            (Appointment.status == AppointmentStatus.COMPLETED, Appointment.price_total_tiyin),
            else_=0,
        )
        tr_lang = aliased(SpecializationTranslation)
        tr_ru = aliased(SpecializationTranslation)
        rows = (
            await self._session.execute(
                select(
                    Membership.id,
                    Account.full_name,
                    func.coalesce(tr_lang.value, tr_ru.value),
                    func.count(Appointment.id),
                    func.coalesce(func.sum(completed), 0),
                    func.coalesce(func.sum(cancelled), 0),
                    func.coalesce(func.sum(charged), 0),
                    func.coalesce(paid_sq.c.paid, 0),
                    func.coalesce(func.sum(no_show), 0),
                    func.coalesce(func.sum(booked), 0),
                )
                .select_from(Appointment)
                .join(Membership, Membership.id == Appointment.doctor_membership_id)
                .join(Account, Account.id == Membership.account_id)
                .outerjoin(Specialization, Specialization.id == Membership.specialization_id)
                .outerjoin(
                    tr_lang,
                    (tr_lang.specialization_id == Specialization.id) & (tr_lang.lang == lang),
                )
                .outerjoin(
                    tr_ru,
                    (tr_ru.specialization_id == Specialization.id) & (tr_ru.lang == Lang.RU),
                )
                .outerjoin(paid_sq, paid_sq.c.membership_id == Membership.id)
                .where(*self._appointment_filters(clinic_id, start, end, own_membership_id))
                .group_by(
                    Membership.id, Account.full_name, tr_lang.value, tr_ru.value, paid_sq.c.paid
                )
                .order_by(func.coalesce(func.sum(charged), 0).desc())
            )
        ).all()
        result: list[DoctorReportRow] = []
        for r in rows:
            charged_total, paid_total, completed_count = int(r[6]), int(r[7]), int(r[4])
            result.append(
                DoctorReportRow(
                    membership_id=r[0],
                    full_name=r[1],
                    specialization_name=r[2],
                    appointments=int(r[3]),
                    completed=completed_count,
                    cancelled=int(r[5]),
                    no_show=int(r[8]),
                    charged_tiyin=charged_total,
                    paid_tiyin=paid_total,
                    debt_tiyin=charged_total - paid_total,
                    average_check_tiyin=charged_total // completed_count if completed_count else 0,
                    booked_minutes=int(r[9]),
                    working_minutes=None,
                    workload_percent=None,
                )
            )
        return result

    # ── Услуги ─────────────────────────────────────────────────────────────
    async def services(
        self,
        clinic_id: uuid.UUID,
        start: datetime | None,
        end: datetime,
        own_membership_id: uuid.UUID | None,
    ) -> list[ServiceReportRow]:
        """Топ услуг периода по позициям приёмов: сколько раз оказали и на сколько.

        Считается по **количеству**, а не по числу строк: «снимок ×3» — это три
        оказанные услуги одной позицией, иначе отчёт занижает и работу, и выручку.
        """
        quantity = func.coalesce(func.sum(AppointmentItem.quantity), 0)
        revenue = func.coalesce(
            func.sum(AppointmentItem.price_tiyin * AppointmentItem.quantity), 0
        )
        rows = (
            await self._session.execute(
                select(
                    AppointmentItem.service_id,
                    func.coalesce(Service.name, literal(_NO_SERVICE)),
                    quantity,
                    revenue,
                )
                .select_from(AppointmentItem)
                .join(Appointment, Appointment.id == AppointmentItem.appointment_id)
                .outerjoin(Service, Service.id == AppointmentItem.service_id)
                .where(*self._appointment_filters(clinic_id, start, end, own_membership_id))
                .group_by(AppointmentItem.service_id, Service.name)
                .order_by(revenue.desc())
            )
        ).all()
        return [
            ServiceReportRow(service_id=r[0], name=r[1], count=int(r[2]), revenue_tiyin=int(r[3]))
            for r in rows
        ]

    # ── Платформенный свод по клиникам ─────────────────────────────────────
    async def clinics(
        self, start: datetime | None, end: datetime, *, lang: Lang
    ) -> list[ClinicReportRow]:
        """Ключевые метрики по каждой клинике платформы за период."""
        pay_conditions: list[ColumnElement[bool]] = [CashPayment.created_at < end]
        appt_conditions: list[ColumnElement[bool]] = [Appointment.scheduled_at < end]
        if start is not None:
            pay_conditions.append(CashPayment.created_at >= start)
            appt_conditions.append(Appointment.scheduled_at >= start)

        revenue_sq = (
            select(
                CashPayment.clinic_id.label("clinic_id"),
                func.coalesce(func.sum(_signed_amount()), 0).label("net"),
            )
            .where(*pay_conditions)
            .group_by(CashPayment.clinic_id)
            .subquery()
        )
        completed = case((Appointment.status == AppointmentStatus.COMPLETED, 1), else_=0)
        charged = case(
            (Appointment.status == AppointmentStatus.COMPLETED, Appointment.price_total_tiyin),
            else_=0,
        )
        appt_sq = (
            select(
                Appointment.clinic_id.label("clinic_id"),
                func.count().label("appointments"),
                func.coalesce(func.sum(completed), 0).label("completed"),
                func.coalesce(func.sum(charged), 0).label("charged"),
                func.count(func.distinct(Appointment.patient_id)).label("patients"),
            )
            .where(*appt_conditions)
            .group_by(Appointment.clinic_id)
            .subquery()
        )
        # Долг клиники = начислено по завершённым приёмам периода − оплачено по ним.
        paid_completed_sq = (
            select(
                Appointment.clinic_id.label("clinic_id"),
                func.coalesce(func.sum(_signed_amount()), 0).label("paid"),
            )
            .select_from(CashPayment)
            .join(Appointment, Appointment.id == CashPayment.appointment_id)
            .where(*appt_conditions, Appointment.status == AppointmentStatus.COMPLETED)
            .group_by(Appointment.clinic_id)
            .subquery()
        )
        doctors_sq = (
            select(Membership.clinic_id.label("clinic_id"), func.count().label("doctors"))
            .where(Membership.active.is_(True), Membership.specialization_id.is_not(None))
            .group_by(Membership.clinic_id)
            .subquery()
        )
        tr_lang = aliased(CityTranslation)
        tr_ru = aliased(CityTranslation)
        rows = (
            await self._session.execute(
                select(
                    Clinic.id,
                    Clinic.name,
                    func.coalesce(tr_lang.value, tr_ru.value),
                    func.coalesce(revenue_sq.c.net, 0),
                    func.coalesce(appt_sq.c.charged, 0),
                    func.coalesce(paid_completed_sq.c.paid, 0),
                    func.coalesce(appt_sq.c.appointments, 0),
                    func.coalesce(appt_sq.c.completed, 0),
                    func.coalesce(doctors_sq.c.doctors, 0),
                    func.coalesce(appt_sq.c.patients, 0),
                )
                .outerjoin(revenue_sq, revenue_sq.c.clinic_id == Clinic.id)
                .outerjoin(appt_sq, appt_sq.c.clinic_id == Clinic.id)
                .outerjoin(paid_completed_sq, paid_completed_sq.c.clinic_id == Clinic.id)
                .outerjoin(doctors_sq, doctors_sq.c.clinic_id == Clinic.id)
                .outerjoin(City, City.id == Clinic.city_id)
                .outerjoin(tr_lang, (tr_lang.city_id == City.id) & (tr_lang.lang == lang))
                .outerjoin(tr_ru, (tr_ru.city_id == City.id) & (tr_ru.lang == Lang.RU))
                .order_by(func.coalesce(revenue_sq.c.net, 0).desc(), Clinic.name)
            )
        ).all()
        return [
            ClinicReportRow(
                clinic_id=r[0],
                name=r[1],
                city_name=r[2],
                revenue_net_tiyin=int(r[3]),
                debt_tiyin=int(r[4]) - int(r[5]),
                appointments=int(r[6]),
                completed=int(r[7]),
                doctors=int(r[8]),
                patients=int(r[9]),
            )
            for r in rows
        ]

    # ── Справочное ─────────────────────────────────────────────────────────
    async def clinic_context(self, clinic_id: uuid.UUID) -> tuple[str, Lang] | None:
        """Таймзона и язык клиники (для периодов и локализации названий)."""
        row = (
            await self._session.execute(
                select(Clinic.timezone, Clinic.lang).where(Clinic.id == clinic_id)
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def own_membership(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> uuid.UUID | None:
        """Собственное активное членство в клинике — «свой» для reports.view_own."""
        return (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.account_id == account_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                )
            )
        ).scalar_one_or_none()
