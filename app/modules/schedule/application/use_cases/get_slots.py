"""Use-case: свободные слоты врача на день (и пакетно — на несколько дней)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.schedule.application.services.availability_service import AvailabilityService
from app.modules.schedule.domain.availability import doctor_slots
from app.modules.schedule.domain.models import DaySlots, TimeSpan
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


@dataclass(frozen=True, slots=True)
class _SlotTarget:
    """Разобранный запрос слотов: таймзона клиники, длительность и кого смотреть."""

    tz: ZoneInfo
    #: Имя таймзоны для ответа — как записано у клиники.
    timezone: str
    duration_min: int
    doctors: list[uuid.UUID]


class GetSlotsUseCase:
    def __init__(self, schedule: ScheduleRepository, availability: AvailabilityService) -> None:
        self._schedule = schedule
        self._availability = availability

    async def execute(
        self,
        clinic_id: uuid.UUID,
        doctor_membership_id: uuid.UUID | None,
        day: date,
        service_id: uuid.UUID | None,
        teeth_count: int = 1,
    ) -> DaySlots:
        """Слоты одного врача либо всех, кто выполняет услугу.

        Без ``doctor_membership_id`` нужна услуга: подбирать «ближайшее свободное»
        по всей клинике без неё — это сетка регистратуры, а не запись, и врача в
        ней всё равно пришлось бы выбирать руками.

        ``teeth_count`` умножает длительность услуги: она задана на один зуб, и
        без множителя регистратор выбирал бы слот на один зуб для работы по трём —
        запись в него потом не помещается.
        """
        target = await self._resolve(clinic_id, doctor_membership_id, service_id, teeth_count)
        free = [
            (doctor, await self._availability.free_spans(clinic_id, doctor, day, target.tz))
            for doctor in target.doctors
        ]
        return _day_slots(target, day, doctor_membership_id, free)

    async def execute_days(
        self,
        clinic_id: uuid.UUID,
        doctor_membership_id: uuid.UUID | None,
        days: Sequence[date],
        service_id: uuid.UUID | None,
        teeth_count: int = 1,
        *,
        any_doctor: bool = False,
    ) -> list[DaySlots]:
        """Слоты на каждый из ``days`` — те же, что дал бы :meth:`execute` по дням.

        Разница только в загрузке: часы, графики, отсутствия и занятость всех
        врачей поднимаются на весь период разом (:meth:`AvailabilityService.free_spans_many`),
        и число запросов не растёт ни с числом врачей, ни с числом дней.

        ``any_doctor`` — для подбора без врача и без услуги (ассистент на этапе
        «когда удобно?», шаблоны медфильтра): тогда смотрим всех практикующих
        врачей клиники с длительностью по умолчанию. Без флага действует то же
        правило «нужен врач или услуга», что и в :meth:`execute`.
        """
        target = await self._resolve(
            clinic_id, doctor_membership_id, service_id, teeth_count, any_doctor=any_doctor
        )
        free = await self._availability.free_spans_many(clinic_id, target.doctors, days, target.tz)
        return [
            _day_slots(
                target,
                day,
                doctor_membership_id,
                [(doctor, free[day].get(doctor, [])) for doctor in target.doctors],
            )
            for day in days
        ]

    async def _resolve(
        self,
        clinic_id: uuid.UUID,
        doctor_membership_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
        teeth_count: int,
        *,
        any_doctor: bool = False,
    ) -> _SlotTarget:
        """Проверить запрос и решить, чьи слоты и какой длины считать."""
        if doctor_membership_id is None and service_id is None and not any_doctor:
            raise ValidationAppError(
                "Укажите врача или услугу: без них подбирать слоты не по чему",
                code="doctor_or_service_required",
            )
        # Врач обязан быть из этой клиники — иначе чужое расписание (IDOR). Ответ
        # тот же, что и на несуществующего врача: существование чужих не
        # подтверждаем, и такой же 404 отдают остальные ручки по врачу.
        if doctor_membership_id is not None and not await self._schedule.is_doctor_in_clinic(
            clinic_id, doctor_membership_id
        ):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")
        if service_id is not None:
            exists, _ = await self._schedule.service_duration(clinic_id, service_id)
            if not exists:
                raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")

        # Клиника держится ссылкой: identity map сессии хранит объекты слабо, и
        # без неё clinic_timezone и имя таймзоны для ответа читали бы её дважды.
        clinic = await self._schedule.get_clinic(clinic_id)
        tz = await self._availability.clinic_timezone(clinic_id)
        duration_min = await self._availability.resolve_duration(
            clinic_id, service_id, None, units=teeth_count
        )
        if doctor_membership_id is not None:
            doctors = [doctor_membership_id]
        elif service_id is not None:
            doctors = await self._schedule.doctors_for_service(clinic_id, service_id)
        else:
            doctors = await self._schedule.practitioner_ids(clinic_id)
        return _SlotTarget(
            tz=tz,
            timezone=clinic.timezone if clinic is not None else str(tz),
            duration_min=duration_min,
            doctors=doctors,
        )


def _day_slots(
    target: _SlotTarget,
    day: date,
    doctor_membership_id: uuid.UUID | None,
    free: Sequence[tuple[uuid.UUID, Sequence[TimeSpan]]],
) -> DaySlots:
    """Собрать ответ дня из свободного времени врачей (в порядке ``target.doctors``)."""
    return DaySlots(
        day=day,
        timezone=target.timezone,
        doctor_membership_id=doctor_membership_id,
        duration_min=target.duration_min,
        slots=doctor_slots(free, target.duration_min),
    )
