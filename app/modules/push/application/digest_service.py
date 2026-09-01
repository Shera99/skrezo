"""Сводки дня (digest.morning / digest.evening) — персональные события от cron'а.

Утренняя — за час до открытия клиники (план на сегодня), вечерняя — в закрытие
(план на завтра). Момент считается по графику и таймзоне КАЖДОЙ клиники:
единый UTC-cron ломается на первой клинике в другом поясе. Выходной день —
сводок нет; пустой план — тоже (нулевой день — шум, ТЗ-СУ §13).

Сервис лишь ЭМИТИТ события в outbox — дальше обычный конвейер: диспетчер
раскроет адресата (``Audience.ACCOUNT``), утренняя уйдёт отложенным пушем с
отменой по показу, вечерняя останется в инбоксе (low). Повторной отправке
мешает ``push_event``: одна сводка на клинику в день, какой бы тик cron'а её
ни увидел.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.application.outbox import emit_event
from app.modules.schedule.domain.availability import default_working_hours
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_working_hours import ClinicWorkingHours
from app.shared.db.models.membership import Membership
from app.shared.db.models.push_event import PushEvent
from app.shared.db.models.role import Role

logger = structlog.get_logger(__name__)

#: Окно совпадения с целевым моментом: cron бьётся раз в 5 минут, окно шире тика.
_WINDOW = timedelta(minutes=15)
#: Утренняя сводка — за час до открытия клиники.
_MORNING_LEAD = timedelta(hours=1)

#: Статусы, которые в план дня не попадают.
_EXCLUDED = (
    AppointmentStatus.DRAFT,
    AppointmentStatus.CANCELLED,
    AppointmentStatus.NO_SHOW,
)


@dataclass(frozen=True, slots=True)
class _PlanRow:
    account_id: uuid.UUID
    count: int
    first_at: datetime


class DigestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(self, now: datetime | None = None) -> int:
        """Пройти все клиники; вернуть число эмитированных событий."""
        now = now or datetime.now(UTC)
        emitted = 0
        clinics = (
            await self._session.execute(select(Clinic.id, Clinic.timezone))
        ).all()
        for clinic_id, tz_name in clinics:
            try:
                tz = ZoneInfo(tz_name or "Asia/Bishkek")
            except (KeyError, ValueError):
                tz = ZoneInfo("Asia/Bishkek")
            emitted += await self._run_clinic(clinic_id, now.astimezone(tz), tz)
        await self._session.commit()
        return emitted

    async def _run_clinic(self, clinic_id: uuid.UUID, local: datetime, tz: ZoneInfo) -> int:
        hours = await self._hours(clinic_id, local.weekday())
        if hours is None:  # выходной — сводок нет
            return 0
        opens_at, closes_at = hours
        today = local.date()
        emitted = 0
        targets = (
            # (тип, момент сводки, день плана)
            (
                "digest.morning",
                datetime.combine(today, opens_at, tzinfo=tz) - _MORNING_LEAD,
                today,
            ),
            (
                "digest.evening",
                datetime.combine(today, closes_at, tzinfo=tz),
                today + timedelta(days=1),
            ),
        )
        for kind, target_at, plan_date in targets:
            if not (target_at <= local < target_at + _WINDOW):
                continue
            dedup = f"{kind}:{clinic_id}:{today.isoformat()}"
            if await self._already_sent(dedup):
                continue
            emitted += await self._emit_plans(clinic_id, kind, plan_date, tz)
            self._session.add(
                PushEvent(id=dedup, type=kind, clinic_id=clinic_id, dedup_key=dedup)
            )
            await self._session.flush()
        return emitted

    async def _hours(self, clinic_id: uuid.UUID, weekday: int) -> tuple[time, time] | None:
        """Часы дня недели (свои или дефолтные); ``None`` — выходной."""
        row = (
            await self._session.execute(
                select(
                    ClinicWorkingHours.is_working,
                    ClinicWorkingHours.opens_at,
                    ClinicWorkingHours.closes_at,
                ).where(
                    ClinicWorkingHours.clinic_id == clinic_id,
                    ClinicWorkingHours.weekday == weekday,
                )
            )
        ).first()
        if row is None:
            default = default_working_hours(weekday)
            if not default.is_working:
                return None
            return default.opens_at, default.closes_at
        if not row[0]:
            return None
        return row[1], row[2]

    async def _already_sent(self, dedup: str) -> bool:
        return (
            await self._session.execute(select(PushEvent.id).where(PushEvent.id == dedup))
        ).scalar_one_or_none() is not None

    async def _emit_plans(
        self, clinic_id: uuid.UUID, kind: str, plan_date: date, tz: ZoneInfo
    ) -> int:
        """Эмитить персональные сводки: врачам — свои приёмы, ролям — вся клиника."""
        day_start = datetime.combine(plan_date, time(0, 0), tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        rows = (
            await self._session.execute(
                select(
                    Membership.account_id,
                    func.count().label("cnt"),
                    func.min(Appointment.scheduled_at).label("first_at"),
                )
                .join(Membership, Membership.id == Appointment.doctor_membership_id)
                .where(
                    Appointment.clinic_id == clinic_id,
                    Appointment.scheduled_at >= day_start,
                    Appointment.scheduled_at < day_end,
                    Appointment.status.not_in(_EXCLUDED),
                    Membership.active.is_(True),
                )
                .group_by(Membership.account_id)
            )
        ).all()
        if not rows:
            return 0  # пустой план — никому ничего (нулевой день = шум)

        plans = [_PlanRow(account_id=r[0], count=r[1], first_at=r[2]) for r in rows]
        total = sum(p.count for p in plans)
        first_overall = min(p.first_at for p in plans)

        # Менеджеры и владелец видят план всей клиники одной строкой.
        managers = (
            await self._session.execute(
                select(Membership.account_id)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    or_(Role.is_owner.is_(True), Role.key == "manager"),
                )
            )
        ).scalars()

        payloads: dict[uuid.UUID, dict[str, object]] = {
            p.account_id: {"count": p.count, "firstTime": self._hhmm(p.first_at, tz)}
            for p in plans
        }
        for account_id in managers:
            # Врач-владелец получает одну сводку — общую (перезапись словаря).
            payloads[account_id] = {
                "count": total,
                "firstTime": self._hhmm(first_overall, tz),
            }

        for account_id, payload in payloads.items():
            await emit_event(
                self._session,
                clinic_id=clinic_id,
                type_=kind,
                entity_type="digest",
                payload={"accountId": str(account_id), **payload},
            )
        return len(payloads)

    @staticmethod
    def _hhmm(moment: datetime, tz: ZoneInfo) -> str:
        return moment.astimezone(tz).strftime("%H:%M")
