"""Use-case: история записей по конкретному зубу пациента."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.patients.domain.teeth import is_valid_tooth
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.tooth_record import ToothRecord


class ToothHistoryUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, tooth: int
    ) -> Sequence[ToothRecord]:
        if not is_valid_tooth(tooth):
            raise ValidationAppError("Некорректный номер зуба (FDI)", code="invalid_tooth")
        patient = await self._patients.get_patient(clinic_id, patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        return await self._patients.tooth_history(patient_id, tooth)
