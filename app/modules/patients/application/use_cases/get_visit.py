"""Use-case: детали визита (данные + записи по зубам)."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.patients.domain.models import VisitDetail
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


class GetVisitUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, visit_id: uuid.UUID
    ) -> VisitDetail:
        patient = await self._patients.get_patient(clinic_id, patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден", code="patient_not_found")
        visit = await self._patients.get_visit(clinic_id, patient_id, visit_id)
        if visit is None:
            raise NotFoundError("Визит не найден", code="visit_not_found")
        doctor_name = await self._patients.visit_doctor_name(visit)
        records = list(await self._patients.visit_records(visit_id))
        return VisitDetail(visit=visit, doctor_name=doctor_name, records=records)
