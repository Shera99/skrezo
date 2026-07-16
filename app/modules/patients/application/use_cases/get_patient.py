"""Use-case: карточка пациента (глобальный + одонтограмма). Требует связи с клиникой."""

from __future__ import annotations

import uuid

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.patients.domain.models import PatientCard
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class GetPatientUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> PatientCard:
        patient = await self._patients.get(patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        if not await self._patients.clinic_treats(clinic_id, patient_id):
            raise ForbiddenError(
                "Нет приёмов с этим пациентом", code="no_patient_relationship"
            )
        odontogram = list(await self._patients.odontogram(patient_id))
        appts = await self._patients.appointments_count(clinic_id, patient_id)
        return PatientCard(patient=patient, odontogram=odontogram, appointments_count=appts)
