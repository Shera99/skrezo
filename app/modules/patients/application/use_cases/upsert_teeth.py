"""Use-case: создать/обновить одонтограмму (первичная карта зубов или правка)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.patients.domain.models import ToothStatePatch
from app.modules.patients.domain.teeth import is_valid_tooth
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient_tooth import PatientTooth


class UpsertTeethUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, teeth: list[ToothStatePatch]
    ) -> Sequence[PatientTooth]:
        patient = await self._patients.get(patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        if not await self._patients.clinic_treats(clinic_id, patient_id):
            raise ForbiddenError("Нет приёмов с этим пациентом", code="no_patient_relationship")
        for patch in teeth:
            if not is_valid_tooth(patch.tooth_number):
                raise ValidationAppError(
                    f"Некорректный номер зуба (FDI): {patch.tooth_number}", code="invalid_tooth"
                )
        await self._patients.upsert_teeth(patient_id, teeth)
        await self._session.commit()
        return await self._patients.odontogram(patient_id)
