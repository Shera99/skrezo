"""Состояние тихих часов клиники — график + таймзона из БД, правила из domain."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.domain.quiet import clinic_quiet
from app.modules.schedule.domain.availability import default_working_hours
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_working_hours import ClinicWorkingHours


@dataclass(frozen=True, slots=True)
class ClinicMoment:
    """Локальный момент клиники и тихо ли в нём."""

    local_time: time
    quiet: bool


class QuietService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def moment(self, clinic_id: uuid.UUID, now: datetime) -> ClinicMoment:
        """Локальное время клиники и тишина по её графику (дефолт — 09–18, вс вых.)."""
        tz_name = (
            await self._session.execute(select(Clinic.timezone).where(Clinic.id == clinic_id))
        ).scalar_one_or_none()
        try:
            tz = ZoneInfo(tz_name or "Asia/Bishkek")
        except (KeyError, ValueError):
            tz = ZoneInfo("Asia/Bishkek")
        local = now.astimezone(tz)
        row = (
            await self._session.execute(
                select(
                    ClinicWorkingHours.is_working,
                    ClinicWorkingHours.opens_at,
                    ClinicWorkingHours.closes_at,
                ).where(
                    ClinicWorkingHours.clinic_id == clinic_id,
                    ClinicWorkingHours.weekday == local.weekday(),
                )
            )
        ).first()
        if row is None:
            default = default_working_hours(local.weekday())
            is_working, opens_at, closes_at = (
                default.is_working,
                default.opens_at,
                default.closes_at,
            )
        else:
            is_working, opens_at, closes_at = row[0], row[1], row[2]
        return ClinicMoment(
            local_time=local.time(),
            quiet=clinic_quiet(
                is_working=is_working,
                opens_at=opens_at,
                closes_at=closes_at,
                local_time=local.time(),
            ),
        )
