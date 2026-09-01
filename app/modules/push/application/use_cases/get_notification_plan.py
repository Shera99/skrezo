"""Use-case: серверный план локальных напоминаний на день (ТЗ-СУ §8, ADR-5).

«Сервер решает, устройство исполняет»: напоминания по времени («приём через
15 минут») срабатывают локально — сеть в нужную минуту не обязательна, — но
СОСТАВ и ВРЕМЯ решает сервер, у которого правда о расписании. Устройство лишь
ставит будильники по плану и перечитывает его на переднем плане, по событию
сокета и периодически (тихие пуши для этого запрещены, §7).

Горизонт — один день: чем короче, тем уже окно протухания (Д10). Тексты
готовит сервер (title/body/speak из каталога) — та же граница, что у пушей;
деградацию по возрасту ``generated_at`` (>6 часов — «Проверьте расписание»)
делает клиент, серверу об этом знать не нужно.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.push.domain import catalog
from app.modules.schedule.application.services.availability_service import AvailabilityService
from app.shared.db.enums import AppointmentStatus, Lang
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.patient import Patient

#: «Скоро приём» — за 15 минут до начала (каталог старого ADR, тип ``soon``).
SOON_LEAD = timedelta(minutes=15)
#: «Приём должен был начаться» — через 5 минут после начала (тип ``arrival``).
ARRIVAL_DELAY = timedelta(minutes=5)

#: В план попадают только приёмы, о которых ещё ЕСТЬ смысл напоминать:
#: пришедший или идущий приём напоминания не требует, отменённый — тем более.
#: Проверить статус в момент срабатывания локальное уведомление не может —
#: поэтому фильтр на составлении, а протухание закрывают частые перечитывания
#: (горизонт один день + обновление на переднем плане).
_PLANNABLE = (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED)


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Одно локальное уведомление серверного плана."""

    id: str  # стабильный: soon:<appointmentId> — клиент заменяет, не дублирует
    type: str  # soon | arrival
    at: datetime
    payload: dict[str, str]


@dataclass(frozen=True, slots=True)
class NotificationPlan:
    generated_at: datetime
    items: list[PlanItem]


class GetNotificationPlanUseCase:
    def __init__(self, session: AsyncSession, availability: AvailabilityService) -> None:
        self._session = session
        self._availability = availability

    async def execute(
        self,
        clinic_id: uuid.UUID,
        doctor_membership_id: uuid.UUID,
        day: date,
        lang: Lang,
    ) -> NotificationPlan:
        """План напоминаний врача на день (границы дня — в поясе клиники)."""
        if not await self._availability.is_doctor(clinic_id, doctor_membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")
        tz = await self._availability.clinic_timezone(clinic_id)
        day_start = datetime.combine(day, time(0, 0), tzinfo=tz)
        day_end = day_start + timedelta(days=1)

        rows = (
            await self._session.execute(
                select(
                    Appointment.id,
                    Appointment.scheduled_at,
                    func.concat_ws(" ", Patient.last_name, Patient.first_name),
                )
                .join(Patient, Patient.id == Appointment.patient_id)
                .where(
                    Appointment.clinic_id == clinic_id,
                    Appointment.doctor_membership_id == doctor_membership_id,
                    Appointment.scheduled_at >= day_start,
                    Appointment.scheduled_at < day_end,
                    Appointment.status.in_(_PLANNABLE),
                )
                .order_by(Appointment.scheduled_at)
            )
        ).all()

        items: list[PlanItem] = []
        for appointment_id, scheduled_at, patient_name in rows:
            params = {
                "patientName": (patient_name or "").strip() or "пациент",
                "time": scheduled_at.astimezone(tz).strftime("%H:%M"),
            }
            base_payload = {
                "appointmentId": str(appointment_id),
                "clinicId": str(clinic_id),
                **params,
            }
            for kind, event_type, at in (
                ("soon", "appointment.soon", scheduled_at - SOON_LEAD),
                ("arrival", "appointment.arrival", scheduled_at + ARRIVAL_DELAY),
            ):
                texts = catalog.render(event_type, lang, **params) or {}
                items.append(
                    PlanItem(
                        id=f"{kind}:{appointment_id}",
                        type=kind,
                        at=at,
                        payload={
                            **base_payload,
                            "title": texts.get("title") or "",
                            "body": texts.get("body") or "",
                            "speak": texts.get("speak") or "",
                        },
                    )
                )
        items.sort(key=lambda i: i.at)
        return NotificationPlan(generated_at=datetime.now(UTC), items=items)
