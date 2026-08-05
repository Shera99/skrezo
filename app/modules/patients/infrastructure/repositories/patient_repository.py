"""Репозиторий пациентов (глобальные) + одонтограмма + история зуба.

Пациент глобальный; «пациенты клиники» = у кого есть приём в этой клинике.
История зуба — сквозной JOIN patient_tooth→appointment_tooth→appointment→clinic/
doctor/service (по всем клиникам, никогда не теряется).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.patients.domain.models import (
    PatientListRow,
    ToothHistoryEntry,
    ToothStatePatch,
)
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_tooth import AppointmentTooth
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_tooth import PatientTooth
from app.shared.db.models.service import Service
from app.shared.db.updates import apply_updates

# Колонки пациента, которые разрешено править через PATCH. Здесь нет phone:
# это ключ дедупликации пациента на всю платформу, его правка увела бы карту
# и сквозную историю лечения к чужому человеку.
_UPDATABLE_FIELDS = frozenset(
    {"first_name", "last_name", "middle_name", "gender", "birth_date", "email", "note"}
)
# Из них — NOT NULL в БД.
_NOT_NULL_FIELDS = frozenset({"first_name"})


class PatientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, patient_id: uuid.UUID) -> Patient | None:
        return await self._session.get(Patient, patient_id)

    async def find_by_phone(self, phone: str) -> Patient | None:
        return (
            await self._session.execute(select(Patient).where(Patient.phone == phone))
        ).scalar_one_or_none()

    async def create(self, **kwargs: Any) -> Patient:
        patient = Patient(**kwargs)
        self._session.add(patient)
        await self._session.flush()
        return patient

    async def update(self, patient: Patient, fields: dict[str, Any]) -> None:
        """Записать разрешённые поля пациента."""
        apply_updates(
            patient,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="пациента",
        )
        await self._session.flush()

    async def clinic_treats(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> bool:
        """Есть ли у клиники приём с пациентом (право видеть карту/историю)."""
        row = (
            await self._session.execute(
                select(Appointment.id)
                .where(Appointment.clinic_id == clinic_id, Appointment.patient_id == patient_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return row is not None

    async def list_clinic_patients(
        self, clinic_id: uuid.UUID, *, query: str | None, page: int, page_size: int
    ) -> tuple[list[PatientListRow], int]:
        base = (
            select(Patient)
            .join(Appointment, Appointment.patient_id == Patient.id)
            .where(Appointment.clinic_id == clinic_id)
            .distinct()
        )
        if query:
            like = f"%{query}%"
            base = base.where(
                or_(
                    Patient.first_name.ilike(like),
                    Patient.last_name.ilike(like),
                    Patient.phone.ilike(like),
                )
            )
        total = (
            await self._session.execute(
                select(func.count()).select_from(base.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            (
                await self._session.execute(
                    base.order_by(Patient.created_at.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        items = [
            PatientListRow(
                id=p.id,
                phone=p.phone,
                first_name=p.first_name,
                last_name=p.last_name,
                birth_date=p.birth_date,
                gender=p.gender,
            )
            for p in rows
        ]
        return items, total

    async def odontogram(self, patient_id: uuid.UUID) -> Sequence[PatientTooth]:
        return (
            (
                await self._session.execute(
                    select(PatientTooth)
                    .where(PatientTooth.patient_id == patient_id)
                    .order_by(PatientTooth.tooth_number)
                )
            )
            .scalars()
            .all()
        )

    async def appointments_count(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(Appointment)
                .where(Appointment.clinic_id == clinic_id, Appointment.patient_id == patient_id)
            )
        ).scalar_one()

    async def upsert_teeth(self, patient_id: uuid.UUID, teeth: list[ToothStatePatch]) -> None:
        """Создать/обновить строки одонтограммы (первичная карта или правка)."""
        existing = {t.tooth_number: t for t in await self.odontogram(patient_id)}
        for patch in teeth:
            row = existing.get(patch.tooth_number)
            if row is None:
                self._session.add(
                    PatientTooth(
                        patient_id=patient_id,
                        tooth_number=patch.tooth_number,
                        status=patch.status,
                        note=patch.note,
                    )
                )
            else:
                row.status = patch.status
                if patch.note is not None:
                    row.note = patch.note
        await self._session.flush()

    async def tooth_history(
        self, patient_id: uuid.UUID, tooth_number: int
    ) -> list[ToothHistoryEntry]:
        rows = (
            await self._session.execute(
                select(
                    Appointment.id,
                    Appointment.scheduled_at,
                    Clinic.name,
                    Account.full_name,
                    Service.name,
                    AppointmentTooth.status,
                    AppointmentTooth.price_tiyin,
                    AppointmentTooth.comment,
                )
                .select_from(PatientTooth)
                .join(AppointmentTooth, AppointmentTooth.patient_tooth_id == PatientTooth.id)
                .join(Appointment, Appointment.id == AppointmentTooth.appointment_id)
                .join(Clinic, Clinic.id == Appointment.clinic_id)
                .outerjoin(Membership, Membership.id == AppointmentTooth.doctor_membership_id)
                .outerjoin(Account, Account.id == Membership.account_id)
                .outerjoin(Service, Service.id == AppointmentTooth.service_id)
                .where(
                    PatientTooth.patient_id == patient_id,
                    PatientTooth.tooth_number == tooth_number,
                )
                .order_by(Appointment.scheduled_at.desc())
            )
        ).all()
        return [
            ToothHistoryEntry(
                appointment_id=r[0],
                date=r[1],
                clinic_name=r[2],
                doctor_name=r[3],
                service_name=r[4],
                status=r[5],
                price_tiyin=r[6],
                comment=r[7],
            )
            for r in rows
        ]
