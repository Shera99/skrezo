"""Use-case: глобальный поиск пациента по телефону (для привязки к приёму)."""

from __future__ import annotations

from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient import Patient
from app.shared.domain.value_objects import normalize_phone


class LookupPatientUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(self, phone_raw: str) -> Patient | None:
        return await self._patients.find_by_phone(normalize_phone(phone_raw))
