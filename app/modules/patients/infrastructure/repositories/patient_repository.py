"""Репозиторий пациентов, визитов и записей по зубам (клиничный тенант).

Все выборки ограничены ``clinic_id`` из токена. Одонтограмма и история зуба —
производные от ``tooth_record``; счёт истории и текущий статус считаются в Python
(объёмы на пациента малы).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.patients.domain.models import (
    OdontogramTooth,
    PatientListRow,
    VisitRow,
)
from app.shared.db.models.account import Account
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.tooth_record import ToothRecord
from app.shared.db.models.visit import Visit


class PatientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_patient(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> Patient | None:
        patient = await self._session.get(Patient, patient_id)
        return patient if patient is not None and patient.clinic_id == clinic_id else None

    async def membership_in_clinic(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID
    ) -> bool:
        row = (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.id == membership_id, Membership.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()
        return row is not None

    async def list_patients(
        self,
        clinic_id: uuid.UUID,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PatientListRow], int]:
        stmt = select(Patient).where(Patient.clinic_id == clinic_id)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(or_(Patient.full_name.ilike(like), Patient.phone.ilike(like)))

        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()

        rows = (
            (
                await self._session.execute(
                    stmt.order_by(Patient.created_at.desc(), Patient.id.desc())
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
                full_name=p.full_name,
                phone=p.phone,
                birth_date=p.birth_date,
                gender=p.gender,
                created_at=p.created_at,
            )
            for p in rows
        ]
        return items, total

    async def create_patient(
        self,
        clinic_id: uuid.UUID,
        *,
        full_name: str,
        phone: str | None,
        birth_date: date | None,
        gender: str | None,
        note: str | None,
    ) -> Patient:
        patient = Patient(
            clinic_id=clinic_id,
            full_name=full_name,
            phone=phone,
            birth_date=birth_date,
            gender=gender,
            note=note,
        )
        self._session.add(patient)
        await self._session.flush()
        return patient

    async def update_patient(self, patient: Patient, fields: dict[str, Any]) -> None:
        for key, value in fields.items():
            setattr(patient, key, value)
        await self._session.flush()

    async def odontogram(self, patient_id: uuid.UUID) -> list[OdontogramTooth]:
        rows = (
            await self._session.execute(
                select(ToothRecord.tooth, ToothRecord.status, ToothRecord.created_at)
                .where(ToothRecord.patient_id == patient_id)
                .order_by(ToothRecord.created_at)
            )
        ).all()
        latest: dict[int, OdontogramTooth] = {}
        for tooth, status, created_at in rows:
            prev = latest.get(tooth)
            count = (prev.history_count if prev else 0) + 1
            latest[tooth] = OdontogramTooth(
                tooth=tooth, status=status, history_count=count, last_at=created_at
            )
        return [latest[t] for t in sorted(latest)]

    async def visits_count(self, patient_id: uuid.UUID) -> int:
        return (
            await self._session.execute(
                select(func.count()).select_from(Visit).where(Visit.patient_id == patient_id)
            )
        ).scalar_one()

    async def tooth_history(
        self, patient_id: uuid.UUID, tooth: int
    ) -> Sequence[ToothRecord]:
        return (
            (
                await self._session.execute(
                    select(ToothRecord)
                    .where(ToothRecord.patient_id == patient_id, ToothRecord.tooth == tooth)
                    .order_by(ToothRecord.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def list_visits(self, patient_id: uuid.UUID) -> list[VisitRow]:
        records_count = (
            select(func.count())
            .select_from(ToothRecord)
            .where(ToothRecord.visit_id == Visit.id)
            .scalar_subquery()
        )
        rows = (
            await self._session.execute(
                select(
                    Visit.id,
                    Visit.visited_at,
                    Account.full_name,
                    Visit.complaint,
                    records_count.label("records_count"),
                )
                .outerjoin(Membership, Membership.id == Visit.doctor_membership_id)
                .outerjoin(Account, Account.id == Membership.account_id)
                .where(Visit.patient_id == patient_id)
                .order_by(Visit.visited_at.desc())
            )
        ).all()
        return [
            VisitRow(
                id=r[0],
                visited_at=r[1],
                doctor_name=r[2],
                complaint=r[3],
                records_count=r[4],
            )
            for r in rows
        ]

    async def get_visit(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, visit_id: uuid.UUID
    ) -> Visit | None:
        visit = await self._session.get(Visit, visit_id)
        if visit is None or visit.clinic_id != clinic_id or visit.patient_id != patient_id:
            return None
        return visit

    async def visit_doctor_name(self, visit: Visit) -> str | None:
        if visit.doctor_membership_id is None:
            return None
        return (
            await self._session.execute(
                select(Account.full_name)
                .join(Membership, Membership.account_id == Account.id)
                .where(Membership.id == visit.doctor_membership_id)
            )
        ).scalar_one_or_none()

    async def visit_records(self, visit_id: uuid.UUID) -> Sequence[ToothRecord]:
        return (
            (
                await self._session.execute(
                    select(ToothRecord)
                    .where(ToothRecord.visit_id == visit_id)
                    .order_by(ToothRecord.tooth)
                )
            )
            .scalars()
            .all()
        )

    async def create_visit(
        self,
        clinic_id: uuid.UUID,
        patient_id: uuid.UUID,
        *,
        doctor_membership_id: uuid.UUID | None,
        visited_at: datetime | None,
        complaint: str | None,
        note: str | None,
        records: list[dict[str, Any]],
    ) -> Visit:
        visit = Visit(
            clinic_id=clinic_id,
            patient_id=patient_id,
            doctor_membership_id=doctor_membership_id,
            complaint=complaint,
            note=note,
        )
        if visited_at is not None:
            visit.visited_at = visited_at
        self._session.add(visit)
        await self._session.flush()

        for rec in records:
            self._session.add(
                ToothRecord(
                    clinic_id=clinic_id,
                    patient_id=patient_id,
                    visit_id=visit.id,
                    tooth=rec["tooth"],
                    diagnosis=rec.get("diagnosis"),
                    treatment=rec.get("treatment"),
                    status=rec.get("status"),
                    note=rec.get("note"),
                )
            )
        await self._session.flush()
        return visit
