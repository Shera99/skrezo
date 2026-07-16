"""Use-case: правка глобального пациента (требует связи с клиникой)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient import Patient


class UpdatePatientUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, fields: dict[str, Any]
    ) -> Patient:
        patient = await self._patients.get(patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        if not await self._patients.clinic_treats(clinic_id, patient_id):
            raise ForbiddenError("Нет приёмов с этим пациентом", code="no_patient_relationship")
        await self._patients.update(patient, fields)
        await self._session.commit()
        return patient
