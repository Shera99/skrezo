"""Репозиторий расписания: часы клиники, графики врачей, отсутствия, занятость.

Все выборки скоупятся клиникой из контекста токена. Названия специализаций
локализуются как в справочниках: ``coalesce(язык клиники, ru)``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.schedule.domain.models import (
    AppointmentBlock,
    DoctorHours,
    DoctorRow,
    TimeOffRecord,
    TimeSpan,
    WorkingHours,
)
from app.shared.db.enums import AppointmentStatus, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_working_hours import ClinicWorkingHours
from app.shared.db.models.doctor_schedule import DoctorSchedule
from app.shared.db.models.doctor_time_off import DoctorTimeOff
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.service import Service
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation


def _appointment_range() -> ColumnElement[object]:
    """SQL-выражение ``[scheduled_at, scheduled_at + duration_min)``.

    Вызываем именно ``appointment_tstzrange`` — ту же IMMUTABLE-функцию, что стоит
    в выражении EXCLUDE-констрейнта ``ex_appointment_doctor_overlap`` (миграция 0020).
    Собирать ``tstzrange(...)`` инлайном нельзя: Postgres сопоставляет выражение
    индекса с выражением запроса структурно и инлайн-вариант не матчит — GiST-индекс
    не используется, диапазон уходит в Filter, и каждая проверка занятости читает
    все приёмы врача за всю историю.
    """
    return func.appointment_tstzrange(Appointment.scheduled_at, Appointment.duration_min)


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Клиника ────────────────────────────────────────────────────────────
    async def get_clinic(self, clinic_id: uuid.UUID) -> Clinic | None:
        return await self._session.get(Clinic, clinic_id)

    # ── Часы работы клиники ────────────────────────────────────────────────
    async def working_hours(self, clinic_id: uuid.UUID) -> dict[int, WorkingHours]:
        rows = (
            await self._session.execute(
                select(ClinicWorkingHours)
                .where(ClinicWorkingHours.clinic_id == clinic_id)
                .order_by(ClinicWorkingHours.weekday)
            )
        ).scalars()
        return {
            r.weekday: WorkingHours(
                weekday=r.weekday,
                opens_at=r.opens_at,
                closes_at=r.closes_at,
                break_start=r.break_start,
                break_end=r.break_end,
                is_working=r.is_working,
            )
            for r in rows
        }

    async def replace_working_hours(
        self, clinic_id: uuid.UUID, rows: Sequence[WorkingHours]
    ) -> None:
        """Переписать все 7 дней разом (проще и предсказуемее частичных правок)."""
        await self._session.execute(
            delete(ClinicWorkingHours).where(ClinicWorkingHours.clinic_id == clinic_id)
        )
        await self._session.flush()
        self._session.add_all(
            [
                ClinicWorkingHours(
                    clinic_id=clinic_id,
                    weekday=r.weekday,
                    opens_at=r.opens_at,
                    closes_at=r.closes_at,
                    break_start=r.break_start,
                    break_end=r.break_end,
                    is_working=r.is_working,
                )
                for r in rows
            ]
        )
        await self._session.flush()

    # ── График врача ───────────────────────────────────────────────────────
    async def doctor_hours(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID
    ) -> dict[int, DoctorHours]:
        rows = (
            await self._session.execute(
                select(DoctorSchedule)
                .where(
                    DoctorSchedule.clinic_id == clinic_id,
                    DoctorSchedule.membership_id == membership_id,
                )
                .order_by(DoctorSchedule.weekday)
            )
        ).scalars()
        return {
            r.weekday: DoctorHours(
                weekday=r.weekday,
                starts_at=r.starts_at,
                ends_at=r.ends_at,
                is_working=r.is_working,
            )
            for r in rows
        }

    async def doctor_hours_bulk(
        self, clinic_id: uuid.UUID, weekday: int
    ) -> dict[uuid.UUID, DoctorHours]:
        """Графики всех врачей клиники на один день недели — одним запросом (без N+1)."""
        rows = (
            await self._session.execute(
                select(DoctorSchedule).where(
                    DoctorSchedule.clinic_id == clinic_id,
                    DoctorSchedule.weekday == weekday,
                )
            )
        ).scalars()
        return {
            r.membership_id: DoctorHours(
                weekday=r.weekday,
                starts_at=r.starts_at,
                ends_at=r.ends_at,
                is_working=r.is_working,
            )
            for r in rows
        }

    async def clear_doctor_hours(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> int:
        """Убрать индивидуальный график; вернуть число снятых дней.

        Врач после этого работает по часам клиники — не копией, снятой в момент
        удаления, а самими часами: их отсутствие и означает «как у клиники»
        (см. ветку ``has_individual`` в ленте дня).
        """
        existing = (
            await self._session.execute(
                select(func.count())
                .select_from(DoctorSchedule)
                .where(
                    DoctorSchedule.clinic_id == clinic_id,
                    DoctorSchedule.membership_id == membership_id,
                )
            )
        ).scalar_one()
        if existing:
            await self._session.execute(
                delete(DoctorSchedule).where(
                    DoctorSchedule.clinic_id == clinic_id,
                    DoctorSchedule.membership_id == membership_id,
                )
            )
            await self._session.flush()
        return int(existing)

    async def replace_doctor_hours(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, rows: Sequence[DoctorHours]
    ) -> None:
        await self._session.execute(
            delete(DoctorSchedule).where(
                DoctorSchedule.clinic_id == clinic_id,
                DoctorSchedule.membership_id == membership_id,
            )
        )
        await self._session.flush()
        self._session.add_all(
            [
                DoctorSchedule(
                    clinic_id=clinic_id,
                    membership_id=membership_id,
                    weekday=r.weekday,
                    starts_at=r.starts_at,
                    ends_at=r.ends_at,
                    is_working=r.is_working,
                )
                for r in rows
            ]
        )
        await self._session.flush()

    # ── Врачи ──────────────────────────────────────────────────────────────
    async def doctors(self, clinic_id: uuid.UUID, lang: Lang) -> list[DoctorRow]:
        """Практикующие врачи клиники — колонки календаря.

        Врач = активное членство со специализацией (``specialization_id IS NOT NULL``).
        """
        tr_lang = aliased(SpecializationTranslation)
        tr_ru = aliased(SpecializationTranslation)
        rows = (
            await self._session.execute(
                select(
                    Membership.id,
                    Account.full_name,
                    func.coalesce(tr_lang.value, tr_ru.value).label("specialization_name"),
                )
                .join(Account, Account.id == Membership.account_id)
                .join(Specialization, Specialization.id == Membership.specialization_id)
                .outerjoin(
                    tr_lang,
                    (tr_lang.specialization_id == Specialization.id) & (tr_lang.lang == lang),
                )
                .outerjoin(
                    tr_ru,
                    (tr_ru.specialization_id == Specialization.id) & (tr_ru.lang == Lang.RU),
                )
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Membership.specialization_id.is_not(None),
                )
                .order_by(Account.full_name)
            )
        ).all()
        return [
            DoctorRow(membership_id=r[0], full_name=r[1], specialization_name=r[2])
            for r in rows
        ]

    async def own_membership(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Собственное активное членство аккаунта в этой клинике — «свой» для RBAC.

        Пара ``(account_id, clinic_id)`` уникальна среди активных членств, поэтому
        это ровно один id. Специализация здесь **не** проверяется: «свой» — это «я»,
        а практикующий я врач или нет, решают уже проверки ниже (колонки/слоты).
        """
        return (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.account_id == account_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def is_doctor_in_clinic(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> bool:
        """Проверка принадлежности врача этой клинике — защита от IDOR."""
        return (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.id == membership_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Membership.specialization_id.is_not(None),
                )
            )
        ).scalar_one_or_none() is not None

    # ── Отсутствия ─────────────────────────────────────────────────────────
    async def time_off(
        self, clinic_id: uuid.UUID, membership_ids: Sequence[uuid.UUID], span: TimeSpan
    ) -> dict[uuid.UUID, list[TimeSpan]]:
        """Отсутствия перечисленных врачей, пересекающие интервал, — одним запросом."""
        if not membership_ids:
            return {}
        rows = (
            await self._session.execute(
                select(
                    DoctorTimeOff.membership_id,
                    DoctorTimeOff.starts_at,
                    DoctorTimeOff.ends_at,
                ).where(
                    DoctorTimeOff.clinic_id == clinic_id,
                    DoctorTimeOff.membership_id.in_(membership_ids),
                    DoctorTimeOff.starts_at < span.end,
                    DoctorTimeOff.ends_at > span.start,
                )
            )
        ).all()
        result: dict[uuid.UUID, list[TimeSpan]] = {}
        for membership_id, starts_at, ends_at in rows:
            result.setdefault(membership_id, []).append(TimeSpan(starts_at, ends_at))
        return result

    async def list_time_off_records(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID
    ) -> list[TimeOffRecord]:
        """Все отсутствия врача как записи (с id и причиной), по возрастанию начала."""
        stmt = (
            select(DoctorTimeOff)
            .where(
                DoctorTimeOff.clinic_id == clinic_id,
                DoctorTimeOff.membership_id == membership_id,
            )
            .order_by(DoctorTimeOff.starts_at)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [
            TimeOffRecord(
                id=r.id,
                membership_id=r.membership_id,
                starts_at=r.starts_at,
                ends_at=r.ends_at,
                reason=r.reason,
            )
            for r in rows
        ]

    async def create_time_off(
        self,
        clinic_id: uuid.UUID,
        membership_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        reason: str | None,
    ) -> TimeOffRecord:
        """Создать отсутствие врача; коммит — на прикладном слое."""
        row = DoctorTimeOff(
            clinic_id=clinic_id,
            membership_id=membership_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        return TimeOffRecord(
            id=row.id,
            membership_id=row.membership_id,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            reason=row.reason,
        )

    async def delete_time_off(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, time_off_id: uuid.UUID
    ) -> bool:
        """Удалить отсутствие по id в рамках (клиника, врач). Вернуть, было ли что удалять.

        Скоуп по ``clinic_id`` и ``membership_id`` закрывает IDOR: чужую запись
        (другая клиника/врач) удалить нельзя — она просто не попадёт под условие.
        """
        result = await self._session.execute(
            delete(DoctorTimeOff)
            .where(
                DoctorTimeOff.id == time_off_id,
                DoctorTimeOff.clinic_id == clinic_id,
                DoctorTimeOff.membership_id == membership_id,
            )
            .returning(DoctorTimeOff.id)
        )
        await self._session.flush()
        return result.first() is not None

    # ── Приёмы ─────────────────────────────────────────────────────────────
    async def appointments_of_day(
        self,
        clinic_id: uuid.UUID,
        span: TimeSpan,
        *,
        doctor_membership_id: uuid.UUID | None = None,
    ) -> list[AppointmentBlock]:
        """Приёмы клиники за день — с именем пациента и названием услуги (без N+1)."""
        stmt = (
            select(
                Appointment.id,
                Appointment.doctor_membership_id,
                Appointment.patient_id,
                func.concat_ws(" ", Patient.last_name, Patient.first_name).label("patient_name"),
                Service.name.label("service_name"),
                Appointment.scheduled_at,
                Appointment.duration_min,
                Appointment.status,
                Appointment.appointment_type,
            )
            .join(Patient, Patient.id == Appointment.patient_id)
            .outerjoin(Service, Service.id == Appointment.service_id)
            .where(
                Appointment.clinic_id == clinic_id,
                Appointment.scheduled_at >= span.start,
                Appointment.scheduled_at < span.end,
            )
            .order_by(Appointment.scheduled_at)
        )
        if doctor_membership_id is not None:
            stmt = stmt.where(Appointment.doctor_membership_id == doctor_membership_id)
        rows = (await self._session.execute(stmt)).all()
        return [
            AppointmentBlock(
                id=r[0],
                doctor_membership_id=r[1],
                patient_id=r[2],
                patient_name=(r[3] or "").strip(),
                service_name=r[4],
                start_at=r[5],
                end_at=r[5] + timedelta(minutes=r[6]),
                status=r[7],
                appointment_type=r[8],
            )
            for r in rows
        ]

    async def busy_spans(
        self,
        clinic_id: uuid.UUID,
        membership_id: uuid.UUID,
        span: TimeSpan,
        *,
        exclude_appointment_id: uuid.UUID | None = None,
    ) -> list[TimeSpan]:
        """Занятые интервалы врача, пересекающие ``span``.

        Отменённые приёмы слот не занимают — ровно как в EXCLUDE-констрейнте.
        Диапазонный предикат ловит и приём, начавшийся до ``span.start``.
        """
        stmt = select(Appointment.scheduled_at, Appointment.duration_min).where(
            Appointment.clinic_id == clinic_id,
            Appointment.doctor_membership_id == membership_id,
            Appointment.status != AppointmentStatus.CANCELLED,
            # Черновик (дата пуста) слот не занимает: tstzrange(NULL, …) —
            # безграничный диапазон, он пересёкся бы с любым запросом занятости
            # и обнулил бы все свободные слоты врача.
            Appointment.scheduled_at.is_not(None),
            _appointment_range().op("&&")(func.tstzrange(span.start, span.end)),
        )
        if exclude_appointment_id is not None:
            stmt = stmt.where(Appointment.id != exclude_appointment_id)
        rows = (await self._session.execute(stmt)).all()
        return [TimeSpan(start, start + timedelta(minutes=duration)) for start, duration in rows]

    async def service_duration(
        self, clinic_id: uuid.UUID, service_id: uuid.UUID
    ) -> tuple[bool, int | None]:
        """Вернуть ``(услуга_есть_в_клинике, длительность)``."""
        row = (
            await self._session.execute(
                select(Service.duration).where(
                    Service.id == service_id, Service.clinic_id == clinic_id
                )
            )
        ).first()
        return (row is not None, row[0] if row is not None else None)
