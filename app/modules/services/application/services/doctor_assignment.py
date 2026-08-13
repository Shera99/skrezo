"""Проверка и запись набора врачей услуги — общая для создания и правки.

Различие «ограничения нет» и «ограничение есть, врачей не осталось» держится на
наличии строк в ``service_doctor``. Отсюда правило записи: ``None`` снимает
ограничение, непустой список его задаёт, а **пустой список отвергается**.

Иначе получилась бы ловушка: клиент прислал ``[]`` в значении «эту услугу никто
не делает», строк не сохранилось, и на чтении это вернулось бы как ``null`` —
«делают все», ровно наоборот.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.errors import ValidationAppError
from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository


async def apply_doctor_ids(
    services: ServiceRepository,
    clinic_id: uuid.UUID,
    service_id: uuid.UUID,
    doctor_ids: Sequence[uuid.UUID] | None,
) -> None:
    """Проверить id и заменить набор врачей услуги."""
    if doctor_ids is not None and len(doctor_ids) == 0:
        raise ValidationAppError(
            "Список врачей не может быть пустым: `null` — услугу делают все, "
            "`active: false` — услуга не оказывается",
            code="empty_doctor_ids",
        )
    if doctor_ids:
        unknown = await services.unknown_doctors(clinic_id, doctor_ids)
        if unknown:
            # Перечисляем лишние: клиент прислал набор и должен понять, какой
            # именно id не подошёл, а не гадать по всему списку.
            raise ValidationAppError(
                "Не врачи этой клиники: " + ", ".join(str(u) for u in unknown),
                code="unknown_doctors",
            )
    await services.set_doctors(service_id, doctor_ids)
