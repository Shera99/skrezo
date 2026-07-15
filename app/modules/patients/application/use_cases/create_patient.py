"""Use-case: создать пациента клиники."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient import Patient


class CreatePatientUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        full_name: str,
        phone: str | None,
        birth_date: date | None,
        gender: str | None,
        note: str | None,
    ) -> Patient:
        patient = await self._patients.create_patient(
            clinic_id,
            full_name=full_name,
            phone=phone,
            birth_date=birth_date,
            gender=gender,
            note=note,
        )
        await self._session.commit()
        return patient
