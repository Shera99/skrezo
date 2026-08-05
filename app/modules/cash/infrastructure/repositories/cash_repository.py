"""Репозиторий кассы: платежи/возвраты, остаток по приёму, журнал, сводка, долги.

Все выборки скоупятся клиникой из контекста токена. Остаток приёма считается по
знаковой сумме операций: платёж ``+amount``, возврат ``−amount`` (см. ``_signed``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cash.domain.models import (
    AppointmentBilling,
    AppointmentDebt,
    MethodTotals,
    PaymentRecord,
)
from app.shared.db.enums import AppointmentStatus, PaymentKind, PaymentMethod
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.clinic import DEFAULT_TIMEZONE, Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient


def _signed() -> ColumnElement[int]:
    """Знаковая сумма операции: платёж прибавляет, возврат вычитает."""
    return func.sum(
        case(
            (CashPayment.kind == PaymentKind.PAYMENT, CashPayment.amount_tiyin),
            else_=-CashPayment.amount_tiyin,
        )
    )


def _patient_name() -> ColumnElement[str]:
    return func.concat_ws(" ", Patient.last_name, Patient.first_name)


def _refunded_by_payment() -> Any:
    """Подзапрос «сколько возвращено по каждому платежу».

    Возвратов по одному платежу может быть несколько (частичные), поэтому это
    сумма, а не признак наличия: остаток к возврату = сумма платежа минус она.
    """
    return (
        select(
            CashPayment.refunds_payment_id.label("payment_id"),
            func.sum(CashPayment.amount_tiyin).label("refunded"),
        )
        .where(
            CashPayment.kind == PaymentKind.REFUND,
            CashPayment.refunds_payment_id.is_not(None),
        )
        .group_by(CashPayment.refunds_payment_id)
        .subquery()
    )


class CashRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Таймзона клиники ───────────────────────────────────────────────────
    async def clinic_timezone(self, clinic_id: uuid.UUID) -> str:
        """IANA-таймзона клиники; при отсутствии — дефолт платформы."""
        tz = (
            await self._session.execute(select(Clinic.timezone).where(Clinic.id == clinic_id))
        ).scalar_one_or_none()
        return tz or DEFAULT_TIMEZONE

    # ── «Свой» кассир ──────────────────────────────────────────────────────
    async def own_membership(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> uuid.UUID | None:
        """Активное членство аккаунта в этой клинике — «свой» для RBAC и recorded_by."""
        return (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.account_id == account_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                )
            )
        ).scalar_one_or_none()

    # ── Биллинговое состояние приёма ───────────────────────────────────────
    async def get_billing(
        self, clinic_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> AppointmentBilling | None:
        """Приём этой клиники: цена, пациент, врач и уже оплаченное (иначе ``None``)."""
        row = (
            await self._session.execute(
                select(
                    Appointment.id,
                    Appointment.patient_id,
                    Appointment.doctor_membership_id,
                    Appointment.price_total_tiyin,
                ).where(Appointment.id == appointment_id, Appointment.clinic_id == clinic_id)
            )
        ).first()
        if row is None:
            return None
        return AppointmentBilling(
            appointment_id=row[0],
            patient_id=row[1],
            doctor_membership_id=row[2],
            price_total_tiyin=row[3],
            paid_tiyin=await self.paid_net(appointment_id),
        )

    async def paid_net(self, appointment_id: uuid.UUID) -> int:
        """Оплачено по приёму нетто (Σплатежей − Σвозвратов); нет операций → 0."""
        value = (
            await self._session.execute(
                select(func.coalesce(_signed(), 0)).where(
                    CashPayment.appointment_id == appointment_id
                )
            )
        ).scalar_one()
        return int(value)

    # ── Создание операции ──────────────────────────────────────────────────
    async def create_payment(
        self,
        *,
        clinic_id: uuid.UUID,
        appointment_id: uuid.UUID,
        patient_id: uuid.UUID,
        amount: int,
        method: PaymentMethod,
        kind: PaymentKind,
        recorded_by_membership_id: uuid.UUID | None,
        refunds_payment_id: uuid.UUID | None,
        comment: str | None,
    ) -> PaymentRecord:
        """Записать операцию кассы; коммит — на прикладном слое."""
        row = CashPayment(
            clinic_id=clinic_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
            amount_tiyin=amount,
            method=method,
            kind=kind,
            recorded_by_membership_id=recorded_by_membership_id,
            refunds_payment_id=refunds_payment_id,
            comment=comment,
        )
        self._session.add(row)
        await self._session.flush()
        record = await self._load_record(clinic_id, row.id)
        assert record is not None  # только что вставили
        return record

    # ── Возврат: исходный платёж и уже возвращённое по нему ────────────────
    async def get_payment(
        self, clinic_id: uuid.UUID, payment_id: uuid.UUID, *, for_update: bool = False
    ) -> CashPayment | None:
        """Операция кассы этой клиники по id (для возврата).

        ``for_update`` блокирует строку платежа до конца транзакции. Возвраты
        стали многократными, и «сколько уже вернули» считается запросом: без
        блокировки два одновременных возврата увидели бы один и тот же остаток
        и вместе перевалили бы за сумму платежа. Все возвраты ссылаются на этот
        же платёж, поэтому блокировка одной строки их и сериализует.
        """
        stmt = select(CashPayment).where(
            CashPayment.id == payment_id, CashPayment.clinic_id == clinic_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def refunded_total(self, payment_id: uuid.UUID) -> int:
        """Сколько уже возвращено по этому платежу (0, если возвратов нет)."""
        value = (
            await self._session.execute(
                select(func.coalesce(func.sum(CashPayment.amount_tiyin), 0)).where(
                    CashPayment.refunds_payment_id == payment_id,
                    CashPayment.kind == PaymentKind.REFUND,
                )
            )
        ).scalar_one()
        return int(value)

    # ── Журнал ─────────────────────────────────────────────────────────────
    def _record_columns(self) -> Select[Any]:
        recorder = Account.full_name
        refunded = _refunded_by_payment()
        return (
            select(
                CashPayment.id,
                CashPayment.appointment_id,
                CashPayment.patient_id,
                _patient_name().label("patient_name"),
                CashPayment.amount_tiyin,
                CashPayment.method,
                CashPayment.kind,
                CashPayment.recorded_by_membership_id,
                recorder.label("recorded_by_name"),
                CashPayment.comment,
                CashPayment.created_at,
                CashPayment.refunds_payment_id,
                # Журнал сразу отвечает на вопрос «сколько ещё можно вернуть» —
                # иначе фронт считал бы это сам, собирая возвраты по страницам.
                func.coalesce(refunded.c.refunded, 0).label("refunded"),
            )
            .join(Patient, Patient.id == CashPayment.patient_id)
            .outerjoin(Membership, Membership.id == CashPayment.recorded_by_membership_id)
            .outerjoin(Account, Account.id == Membership.account_id)
            .outerjoin(refunded, refunded.c.payment_id == CashPayment.id)
        )

    @staticmethod
    def _to_record(row: Any) -> PaymentRecord:
        return PaymentRecord(
            id=row[0],
            appointment_id=row[1],
            patient_id=row[2],
            patient_name=(row[3] or "").strip(),
            amount_tiyin=row[4],
            method=row[5],
            kind=row[6],
            recorded_by_membership_id=row[7],
            recorded_by_name=row[8],
            comment=row[9],
            created_at=row[10],
            refunds_payment_id=row[11],
            refunded_tiyin=int(row[12]),
        )

    async def _load_record(
        self, clinic_id: uuid.UUID, payment_id: uuid.UUID
    ) -> PaymentRecord | None:
        row = (
            await self._session.execute(
                self._record_columns().where(
                    CashPayment.id == payment_id, CashPayment.clinic_id == clinic_id
                )
            )
        ).first()
        return self._to_record(row) if row is not None else None

    def _journal_filters(
        self,
        clinic_id: uuid.UUID,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        patient_id: uuid.UUID | None,
        method: PaymentMethod | None,
        own_membership_id: uuid.UUID | None,
    ) -> list[ColumnElement[bool]]:
        """Общие условия журнала/сводки (клиника + фильтры + own-скоуп)."""
        conditions: list[ColumnElement[bool]] = [CashPayment.clinic_id == clinic_id]
        if date_from is not None:
            conditions.append(CashPayment.created_at >= date_from)
        if date_to is not None:
            conditions.append(CashPayment.created_at < date_to)
        if patient_id is not None:
            conditions.append(CashPayment.patient_id == patient_id)
        if method is not None:
            conditions.append(CashPayment.method == method)
        if own_membership_id is not None:
            conditions.append(CashPayment.recorded_by_membership_id == own_membership_id)
        return conditions

    async def list_payments(
        self,
        clinic_id: uuid.UUID,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        patient_id: uuid.UUID | None,
        method: PaymentMethod | None,
        own_membership_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PaymentRecord], int]:
        """Журнал операций (последние сверху) + общее число под пагинацию."""
        conditions = self._journal_filters(
            clinic_id,
            date_from=date_from,
            date_to=date_to,
            patient_id=patient_id,
            method=method,
            own_membership_id=own_membership_id,
        )
        total = (
            await self._session.execute(
                select(func.count()).select_from(CashPayment).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                self._record_columns()
                .where(*conditions)
                .order_by(CashPayment.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [self._to_record(r) for r in rows], int(total)

    # ── Сводка ─────────────────────────────────────────────────────────────
    async def summary(
        self,
        clinic_id: uuid.UUID,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        own_membership_id: uuid.UUID | None,
    ) -> list[MethodTotals]:
        """Приход/возврат/нетто по каждому способу оплаты за период."""
        conditions = self._journal_filters(
            clinic_id,
            date_from=date_from,
            date_to=date_to,
            patient_id=None,
            method=None,
            own_membership_id=own_membership_id,
        )
        income = func.sum(
            case((CashPayment.kind == PaymentKind.PAYMENT, CashPayment.amount_tiyin), else_=0)
        )
        refund = func.sum(
            case((CashPayment.kind == PaymentKind.REFUND, CashPayment.amount_tiyin), else_=0)
        )
        rows = (
            await self._session.execute(
                select(
                    CashPayment.method,
                    func.coalesce(income, 0),
                    func.coalesce(refund, 0),
                )
                .where(*conditions)
                .group_by(CashPayment.method)
            )
        ).all()
        return [
            MethodTotals(
                method=r[0],
                income_tiyin=int(r[1]),
                refund_tiyin=int(r[2]),
                net_tiyin=int(r[1]) - int(r[2]),
            )
            for r in rows
        ]

    # ── Долги ──────────────────────────────────────────────────────────────
    async def list_debts(
        self,
        clinic_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None,
        own_membership_id: uuid.UUID | None,
    ) -> list[AppointmentDebt]:
        """Завершённые приёмы с ненулевым остатком (price_total − оплачено > 0)."""
        paid_sq = (
            select(
                CashPayment.appointment_id.label("appointment_id"),
                func.coalesce(_signed(), 0).label("paid"),
            )
            .group_by(CashPayment.appointment_id)
            .subquery()
        )
        paid = func.coalesce(paid_sq.c.paid, 0)
        conditions: list[ColumnElement[bool]] = [
            Appointment.clinic_id == clinic_id,
            Appointment.status == AppointmentStatus.COMPLETED,
            (Appointment.price_total_tiyin - paid) > 0,
        ]
        if patient_id is not None:
            conditions.append(Appointment.patient_id == patient_id)
        if own_membership_id is not None:
            conditions.append(Appointment.doctor_membership_id == own_membership_id)
        rows = (
            await self._session.execute(
                select(
                    Appointment.id,
                    Appointment.patient_id,
                    _patient_name().label("patient_name"),
                    Appointment.doctor_membership_id,
                    Appointment.scheduled_at,
                    Appointment.price_total_tiyin,
                    paid.label("paid"),
                )
                .join(Patient, Patient.id == Appointment.patient_id)
                .outerjoin(paid_sq, paid_sq.c.appointment_id == Appointment.id)
                .where(*conditions)
                .order_by(Appointment.scheduled_at.desc())
            )
        ).all()
        return [
            AppointmentDebt(
                appointment_id=r[0],
                patient_id=r[1],
                patient_name=(r[2] or "").strip(),
                doctor_membership_id=r[3],
                scheduled_at=r[4],
                price_total_tiyin=int(r[5]),
                paid_tiyin=int(r[6]),
                outstanding_tiyin=int(r[5]) - int(r[6]),
            )
            for r in rows
        ]
