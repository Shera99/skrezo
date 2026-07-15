"""Use-case: правка карточки пациента."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.patients.domain.models import PatientDetail
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class UpdatePatientUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, fields: dict[str, Any]
    ) -> PatientDetail:
        patient = await self._patients.get_patient(clinic_id, patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        await self._patients.update_patient(patient, fields)
        await self._session.commit()

        odontogram = await self._patients.odontogram(patient_id)
        visits_count = await self._patients.visits_count(patient_id)
        return PatientDetail(patient=patient, odontogram=odontogram, visits_count=visits_count)
