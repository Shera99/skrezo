"""Use-case: список сотрудников клиники."""

from __future__ import annotations

import uuid

from app.modules.staff.domain.models import StaffMember
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


class ListStaffUseCase:
    def __init__(self, staff: StaffRepository) -> None:
        self._staff = staff

    async def execute(self, clinic_id: uuid.UUID) -> list[StaffMember]:
        return await self._staff.list_members(clinic_id)
