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
    clinic_id: uuid.UUID,
    items: Sequence[ItemInput],
    appointments: AppointmentRepository,
    *,
    doctor_id: uuid.UUID | None = None,
    force: bool = False,
) -> None:
    """Проверить зубы, услуги позиций и то, что врач их выполняет; иначе 422.

    Услуги в позициях проверяются наравне с ``serviceId`` приёма, потому что
    именно из них считаются деньги и обновляется карта зуба: без этой проверки
    терапевта записывают на «Осмотр», а в состав приёма кладут «Имплантацию».

    Врач передаётся не всегда: у черновика его может не быть, а ``force``
    (право ``appointments.force_booking``) снимает проверку — как и на записи.
    """
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

    if force or doctor_id is None:
        return
    # Одной ошибкой на весь чек со списком услуг: клиент прислал состав целиком и
    # должен увидеть все проблемные строки сразу, а не чинить их по одной.
    blocked = [
        service_id
        for service_id in dict.fromkeys(i.service_id for i in items if i.service_id is not None)
        if not await appointments.doctor_can_do_service(doctor_id, service_id)
    ]
    if blocked:
        raise ValidationAppError(
            "Врач не выполняет часть услуг из состава приёма",
            code="doctor_cant_do_service",
            extra={"serviceIds": [str(s) for s in blocked]},
        )


async def resolve_service_prices(
    items: Sequence[ItemInput], appointments: AppointmentRepository
) -> dict[uuid.UUID, int]:
    """Цены услуг позиций — их фиксируем в чеке на момент оказания."""
    return await appointments.service_prices(
        [item.service_id for item in items if item.service_id is not None]
    )
