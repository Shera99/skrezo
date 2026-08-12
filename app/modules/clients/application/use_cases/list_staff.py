"""Use-case: сотрудники клиники для админки Skrezo.

Карточка клиента отдаёт тот же список, но целиком вместе с подпиской, кассой и
активностью. Отдельная ручка нужна экрану «сотрудники клиники»: тянуть карточку
ради списка людей — лишний трафик, а фильтр по ``active`` в карточке негде взять.
"""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.clients.domain.models import StaffRow
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.db.enums import Lang


class ListStaffUseCase:
    def __init__(self, clients: ClientRepository) -> None:
        self._clients = clients

    async def execute(
        self, clinic_id: uuid.UUID, *, lang: Lang, active: bool | None = None
    ) -> list[StaffRow]:
        """Список сотрудников клиники; несуществующая клиника → 404.

        Пустой список у существующей клиники законен (соло без сотрудников), и
        отличить его от опечатки в ``clinic_id`` клиент иначе не смог бы.
        """
        if not await self._clients.clinic_exists(clinic_id):
            raise NotFoundError("Клиент не найден", code="client_not_found")
        return await self._clients.staff(clinic_id, lang, active=active)
