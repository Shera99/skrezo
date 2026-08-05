"""Use-case: карточка пациента (глобальный + одонтограмма). Требует связи с клиникой."""

from __future__ import annotations

import uuid

from app.modules.patients.application.services.access import ensure_clinic_patient
from app.modules.patients.domain.models import PatientCard
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class GetPatientUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> PatientCard:
        patient = await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        odontogram = list(await self._patients.odontogram(patient_id))
        appts = await self._patients.appointments_count(clinic_id, patient_id)
        return PatientCard(patient=patient, odontogram=odontogram, appointments_count=appts)
