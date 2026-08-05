"""Проверка позиций чека — общее для создания приёма и правки его позиций.

Правила одни и те же на обоих путях: зуб обязан быть валидным по FDI, услуга —
из этой клиники (иначе IDOR: чужой прайс приехал бы в чек). Держать их в одном
месте важнее обычного — расхождение здесь означает, что через одну ручку в чек
попадает то, что другая не пропускает.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.errors import ValidationAppError
from app.modules.appointments.domain.models import ItemInput
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.patients.domain.teeth import is_valid_tooth


async def ensure_items_valid(
    clinic_id: uuid.UUID, items: Sequence[ItemInput], appointments: AppointmentRepository
) -> None:
    """Проверить зубы и услуги позиций; иначе 422."""
    for item in items:
        for tooth in item.teeth:
            if not is_valid_tooth(tooth):
                raise ValidationAppError(
                    f"Некорректный номер зуба (FDI): {tooth}", code="invalid_tooth"
                )
        if item.service_id is not None and not await appointments.service_in_clinic(
            clinic_id, item.service_id
        ):
            raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")


async def resolve_service_prices(
    items: Sequence[ItemInput], appointments: AppointmentRepository
) -> dict[uuid.UUID, int]:
    """Цены услуг позиций — их фиксируем в чеке на момент оказания."""
    return await appointments.service_prices(
        [item.service_id for item in items if item.service_id is not None]
    )
