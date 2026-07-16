"""Use-case: создать глобального пациента (дедуп по телефону)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient import Patient
from app.shared.domain.value_objects import normalize_phone


class CreatePatientUseCase:
    def __init__(self, session: AsyncSession, patients: PatientRepository) -> None:
        self._session = session
        self._patients = patients

    async def execute(
        self,
        *,
        phone: str,
        first_name: str,
        last_name: str | None,
        middle_name: str | None,
        gender: str | None,
        birth_date: date | None,
        email: str | None,
    ) -> Patient:
        normalized = normalize_phone(phone)
        existing = await self._patients.find_by_phone(normalized)
        if existing is not None:
            # Пациент уже есть на платформе — привязывайтесь по нему (id в ошибке).
            raise ConflictError(
                "Пациент с таким телефоном уже есть",
                code="phone_already_used",
                extra={"patientId": str(existing.id)},
            )
        patient = await self._patients.create(
            phone=normalized,
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            gender=gender,
            birth_date=birth_date,
            email=email,
        )
        await self._session.commit()
        return patient
