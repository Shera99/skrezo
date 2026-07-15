"""Use-case: список пациентов клиники (offset-пагинация + поиск)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.modules.patients.domain.models import PatientListRow
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


@dataclass(slots=True)
class ListPatientsResult:
    items: list[PatientListRow]
    total_count: int
    current_page: int
    page_size: int


class ListPatientsUseCase:
    def __init__(self, patients: PatientRepository) -> None:
        self._patients = patients

    async def execute(
        self, clinic_id: uuid.UUID, *, query: str | None, page: int, page_size: int
    ) -> ListPatientsResult:
        items, total = await self._patients.list_patients(
            clinic_id, query=query, page=page, page_size=page_size
        )
        return ListPatientsResult(items, total, page, page_size)
