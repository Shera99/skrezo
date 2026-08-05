"""Use-case: сквозная история зуба (по всем клиникам). Требует связи с клиникой."""

from __future__ import annotations

import uuid

from app.core.errors import ValidationAppError
from app.modules.patients.application.services.access import ensure_clinic_patient
from app.modules.patients.domain.models import ToothHistoryEntry
from app.modules.patients.domain.teeth import is_valid_tooth
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class ToothHistoryUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, tooth: int
    ) -> list[ToothHistoryEntry]:
        if not is_valid_tooth(tooth):
            raise ValidationAppError("Некорректный номер зуба (FDI)", code="invalid_tooth")
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        return await self._patients.tooth_history(patient_id, tooth)
