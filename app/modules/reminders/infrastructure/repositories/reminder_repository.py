"""Репозиторий отметок «напомнили»: запись, журнал с фильтрами, удаление.

Пациент глобальный, поэтому «пациент клиники» — тот, у кого в этой клинике
есть хотя бы один приём (как в списке пациентов). Отметка по чужому пациенту
не пишется — иначе журнал одной клиники протёк бы в другую.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reminders.domain.models import ReminderKind, ReminderMarkRecord
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import DEFAULT_TIMEZONE, Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.reminder_mark import ReminderMark


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Справочное ─────────────────────────────────────────────────────────
    async def clinic_timezone(self, clinic_id: uuid.UUID) -> str:
        """IANA-таймзона клиники; при отсутствии — дефолт платформы."""
        tz = (
            await self._session.execute(select(Clinic.timezone).where(Clinic.id == clinic_id))
        ).scalar_one_or_none()
        return tz or DEFAULT_TIMEZONE

    async def own_membership(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> uuid.UUID | None:
        """Активное членство аккаунта в клинике — «кто отметил»."""
        return (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.account_id == account_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def clinic_treats(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> bool:
        """Пациент этой клиники — есть хотя бы один её приём (как в списке пациентов)."""
        return (
            await self._session.execute(
                select(Appointment.id)
                .where(Appointment.clinic_id == clinic_id, Appointment.patient_id == patient_id)
                .limit(1)
            )
        ).scalar_one_or_none() is not None

    async def appointment_of(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> bool:
        """Приём принадлежит этой клинике и этому пациенту."""
        return (
            await self._session.execute(
                select(Appointment.id).where(
                    Appointment.id == appointment_id,
                    Appointment.clinic_id == clinic_id,
                    Appointment.patient_id == patient_id,
                )
            )
        ).scalar_one_or_none() is not None

    # ── Запись ─────────────────────────────────────────────────────────────
    async def create(
        self,
        *,
        clinic_id: uuid.UUID,
        patient_id: uuid.UUID,
        kind: ReminderKind,
        appointment_id: uuid.UUID | None,
        comment: str | None,
        marked_by_membership_id: uuid.UUID | None,
    ) -> ReminderMarkRecord:
        """Записать отметку; коммит — на прикладном слое."""
        row = ReminderMark(
            clinic_id=clinic_id,
            patient_id=patient_id,
            kind=kind.value,
            appointment_id=appointment_id,
            comment=comment,
            marked_by_membership_id=marked_by_membership_id,
        )
        self._session.add(row)
        await self._session.flush()
        record = await self._load(clinic_id, row.id)
        assert record is not None  # только что вставили
        return record

    async def get(self, clinic_id: uuid.UUID, mark_id: uuid.UUID) -> ReminderMark | None:
        """Отметка этой клиники по id (для удаления)."""
        return (
            await self._session.execute(
                select(ReminderMark).where(
                    ReminderMark.id == mark_id, ReminderMark.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def delete(self, mark: ReminderMark) -> None:
        """Удалить отметку; коммит — на прикладном слое."""
        await self._session.delete(mark)
        await self._session.flush()

    # ── Журнал ─────────────────────────────────────────────────────────────
    @staticmethod
    def _record_columns() -> Select[Any]:
        return (
            select(
                ReminderMark.id,
                ReminderMark.patient_id,
                func.concat_ws(" ", Patient.last_name, Patient.first_name).label("patient_name"),
                ReminderMark.kind,
                ReminderMark.appointment_id,
                ReminderMark.comment,
                ReminderMark.marked_by_membership_id,
                Account.full_name.label("marked_by_name"),
                ReminderMark.marked_at,
            )
            .join(Patient, Patient.id == ReminderMark.patient_id)
            .outerjoin(Membership, Membership.id == ReminderMark.marked_by_membership_id)
            .outerjoin(Account, Account.id == Membership.account_id)
        )

    @staticmethod
    def _to_record(row: Any) -> ReminderMarkRecord:
        return ReminderMarkRecord(
            id=row[0],
            patient_id=row[1],
            patient_name=(row[2] or "").strip(),
            kind=ReminderKind(row[3]),
            appointment_id=row[4],
            comment=row[5],
            marked_by_membership_id=row[6],
            marked_by_name=row[7],
            marked_at=row[8],
        )

    async def _load(self, clinic_id: uuid.UUID, mark_id: uuid.UUID) -> ReminderMarkRecord | None:
        row = (
            await self._session.execute(
                self._record_columns().where(
                    ReminderMark.id == mark_id, ReminderMark.clinic_id == clinic_id
                )
            )
        ).first()
        return self._to_record(row) if row is not None else None

    async def list_page(
        self,
        clinic_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None,
        kind: ReminderKind | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ReminderMarkRecord], int]:
        """Отметки клиники (последние сверху) с фильтрами + общее число под пагинацию."""
        conditions: list[ColumnElement[bool]] = [ReminderMark.clinic_id == clinic_id]
        if patient_id is not None:
            conditions.append(ReminderMark.patient_id == patient_id)
        if kind is not None:
            conditions.append(ReminderMark.kind == kind.value)
        if date_from is not None:
            conditions.append(ReminderMark.marked_at >= date_from)
        if date_to is not None:
            conditions.append(ReminderMark.marked_at < date_to)
        total = (
            await self._session.execute(
                select(func.count()).select_from(ReminderMark).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                self._record_columns()
                .where(*conditions)
                .order_by(ReminderMark.marked_at.desc(), ReminderMark.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [self._to_record(r) for r in rows], int(total)
