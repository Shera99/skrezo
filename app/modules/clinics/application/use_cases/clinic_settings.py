"""Use-cases настроек своей клиники: показать и обновить.

Клиника ведёт себя сама: название, адрес, телефон, город, язык и **таймзону**.
Таймзона важнее прочего — в ней считаются границы дня, свободные слоты и
периоды отчётов, а менять её через Skrezo на каждый переезд клиники неудобно.
Тариф (``type``, ``seats_limit``) и заметки саппорта остаются за платформенной
зоной: их не отдаёт даже чтение.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.clinics.infrastructure.repositories.clinic_settings_repository import (
    ClinicSettingsRepository,
)
from app.shared.db.enums import Lang
from app.shared.db.models.clinic import Clinic


@dataclass(frozen=True, slots=True)
class ClinicSettingsView:
    """Клиника с названием города на языке запроса."""

    clinic: Clinic
    city_name: str | None


def _ensure_timezone_valid(fields: dict[str, Any]) -> None:
    """Таймзона обязана быть существующей зоной IANA.

    Битое значение не уронит выдачу (сервис доступности откатывается на дефолт),
    но расписание молча уедет в другую таймзону — а это ошибка, которую в UI
    никто не свяжет с настройками.
    """
    timezone = fields.get("timezone")
    if timezone is None:
        return
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationAppError(
            f"Неизвестная таймзона: {timezone}. Ожидается зона IANA, например Asia/Bishkek",
            code="invalid_timezone",
        ) from exc


class GetClinicSettingsUseCase:
    def __init__(self, clinics: ClinicSettingsRepository) -> None:
        self._clinics = clinics

    async def execute(self, clinic_id: uuid.UUID, *, lang: Lang) -> ClinicSettingsView:
        clinic = await self._clinics.get(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        return ClinicSettingsView(
            clinic=clinic, city_name=await self._clinics.city_name(clinic.city_id, lang)
        )


class UpdateClinicSettingsUseCase:
    def __init__(self, session: AsyncSession, clinics: ClinicSettingsRepository) -> None:
        self._session = session
        self._clinics = clinics

    async def execute(
        self, clinic_id: uuid.UUID, fields: dict[str, Any], *, lang: Lang
    ) -> ClinicSettingsView:
        clinic = await self._clinics.get(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")

        _ensure_timezone_valid(fields)
        city_id = fields.get("city_id")
        if city_id is not None and not await self._clinics.city_exists(city_id):
            raise ValidationAppError("Город не найден", code="city_not_found")

        await self._clinics.update(clinic, fields)
        await self._session.commit()
        return ClinicSettingsView(
            clinic=clinic, city_name=await self._clinics.city_name(clinic.city_id, lang)
        )
