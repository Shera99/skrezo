"""Use-case: карточка пациента (глобальный + одонтограмма). Требует связи с клиникой."""

from __future__ import annotations

import uuid

from app.modules.patients.application.services.access import ensure_clinic_patient
from app.modules.patients.domain.models import PatientCard
from app.modules.patients.infrastructure.repositories.patient_file_repository import (
    PatientFileRepository,
)
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class GetPatientUseCase:
    def __init__(self, patients: PatientRepository, files: PatientFileRepository) -> None:
        self._patients = patients
        self._files = files

    async def execute(self, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> PatientCard:
        patient = await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        odontogram = list(await self._patients.odontogram(patient_id))
        appts = await self._patients.appointments_count(clinic_id, patient_id)
        # Счётчики файлов — для меток у зубов в формуле: сами файлы приложение
        # просит отдельно, когда открывают зуб.
        files = await self._files.summary(patient_id, clinic_id)
        return PatientCard(
            patient=patient, odontogram=odontogram, appointments_count=appts, files=files
        )
