"""Use-case: список визитов пациента."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.patients.domain.models import VisitRow
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class ListVisitsUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> list[VisitRow]:
        patient = await self._patients.get_patient(clinic_id, patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        return await self._patients.list_visits(patient_id)
