"""Use-case: частичная правка карточки клиента (клиника + владелец)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.clients.domain.models import ClientDetail
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.db.enums import Lang

# Ключи запроса (спека фронта) → поля модели clinic.
_CLINIC_FIELD_MAP = {
    "name": "name",
    "address": "address",
    "city_id": "city_id",
    "business_phone": "business_phone",
    "language": "lang",
    "notes": "support_notes",
}


class UpdateClientUseCase:
    def __init__(self, session: AsyncSession, clients: ClientRepository) -> None:
        self._session = session
        self._clients = clients

    async def execute(
        self, clinic_id: uuid.UUID, fields: dict[str, Any], *, lang: Lang
    ) -> ClientDetail:
        clinic_fields = {
            _CLINIC_FIELD_MAP[k]: v for k, v in fields.items() if k in _CLINIC_FIELD_MAP
        }
        # Правки уровня владельца (соло: ФИО/специализация приёма).
        owner_full_name = fields.get("admin_full_name")
        set_specialization = "specialization_id" in fields
        owner_spec_id = fields.get("specialization_id")

        if clinic_fields.get("city_id") is not None and not await self._clients.city_exists(
            clinic_fields["city_id"]
        ):
            raise ValidationAppError("Город не найден", code="city_not_found")
        # Специализацию проверяем так же, как город: без этого неизвестный id
        # уходил в базу и падал нарушением внешнего ключа — 500 вместо 422.
        if owner_spec_id is not None and not await self._clients.specialization_exists(
            owner_spec_id
        ):
            raise ValidationAppError(
                "Специализация не найдена", code="specialization_not_found"
            )

        if clinic_fields:
            clinic = await self._clients.update_client(clinic_id, clinic_fields)
            if clinic is None:
                raise NotFoundError("Клиент не найден", code="client_not_found")
        elif await self._clients.get_client(clinic_id, lang=lang) is None:
            raise NotFoundError("Клиент не найден", code="client_not_found")

        if owner_full_name is not None or set_specialization:
            await self._clients.update_owner(
                clinic_id,
                full_name=owner_full_name,
                specialization_id=owner_spec_id,
                set_specialization=set_specialization,
            )

        await self._session.commit()

        detail = await self._clients.get_client(clinic_id, lang=lang)
        if detail is None:  # практически недостижимо после успешного апдейта
            raise NotFoundError("Клиент не найден", code="client_not_found")
        return detail
