"""Use-case: создать визит с набором манипуляций по зубам."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.patients.domain.teeth import is_valid_tooth
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.visit import Visit


@dataclass(slots=True)
class VisitInput:
    doctor_membership_id: uuid.UUID | None
    visited_at: datetime | None
    complaint: str | None
    note: str | None
    records: list[dict[str, Any]]


class CreateVisitUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, data: VisitInput
    ) -> Visit:
        patient = await self._patients.get_patient(clinic_id, patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")

        for rec in data.records:
            if not is_valid_tooth(int(rec["tooth"])):
                raise ValidationAppError(
                    f"Некорректный номер зуба (FDI): {rec['tooth']}", code="invalid_tooth"
                )

        if data.doctor_membership_id is not None and not await self._patients.membership_in_clinic(
            clinic_id, data.doctor_membership_id
        ):
            raise ValidationAppError("Врач не из этой клиники", code="invalid_doctor")

        visit = await self._patients.create_visit(
            clinic_id,
            patient_id,
            doctor_membership_id=data.doctor_membership_id,
            visited_at=data.visited_at,
            complaint=data.complaint,
            note=data.note,
            records=data.records,
        )
        await self._session.commit()
        return visit
