"""Use-case: правка глобального пациента (требует связи с клиникой)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.patients.application.services.access import ensure_clinic_patient
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient import Patient


class UpdatePatientUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, fields: dict[str, Any]
    ) -> Patient:
        patient = await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        await self._patients.update(patient, fields)
        await self._session.commit()
        return patient
