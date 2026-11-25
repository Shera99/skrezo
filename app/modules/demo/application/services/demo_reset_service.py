"""Сброс демо-клиники: убрать показанное и собрать день заново от «сейчас».

Удаление и сборка — одна транзакция: если собрать день нельзя (нет врачей или
прайса), показанное тоже остаётся на месте, а не пустая клиника перед показом.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.appointments.application.services.signals import signal_appointment_updated
from app.modules.demo.domain.day_plan import (
    DAY_PLAN,
    MAX_DOCTORS,
    PEOPLE,
    Payment,
    PlannedVisit,
    anchor,
    demo_phone,
    partial_payment,
    visit_starts,
)
from app.modules.demo.domain.models import Purged, ResetResult
from app.modules.demo.infrastructure.repositories.demo_repository import DemoRepository
from app.modules.schedule.infrastructure.repositories.schedule_repository import (
    ScheduleRepository,
)
from app.shared.db.enums import (
    AppointmentItemStatus,
    AppointmentStatus,
    PaymentKind,
    PaymentMethod,
)
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_item import AppointmentItem
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.patient import Patient
from app.shared.db.models.service import Service
from app.shared.storage.files import resolve_stored_path

logger = structlog.get_logger(__name__)

#: Насколько «раньше» заведены записи и пациенты демо-дня (см. ``backdate``).
_BOOKED_AGO = timedelta(days=2)
_KNOWN_AGO = timedelta(days=30)
#: Сколько номеров пробуем за раз, подбирая свободные.
_PHONE_PAGE = 100


class DemoResetService:
    def __init__(
        self, session: AsyncSession, demo: DemoRepository, schedule: ScheduleRepository
    ) -> None:
        self._session = session
        self._demo = demo
        self._schedule = schedule

    async def reset(
        self, clinic_id: uuid.UUID, now: datetime | None = None, *, backdate: bool = True
    ) -> ResetResult:
        """Собрать день клиники заново.

        ``backdate`` — записи заводятся «позавчера», а не «сейчас»: запись,
        созданная меньше чем за два часа до начала, считается подтверждённой у
        стойки, и «не подтвердил» на показе был бы пуст. Сброс по расписанию
        передаёт ``False`` — в шесть утра пуш «не подтверждён» сотрудникам не нужен.
        """
        now = now or datetime.now(UTC)
        await self._demo.lock()
        clinic = await self._demo.clinic(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        state = await self._demo.state(clinic_id)
        if not clinic.demo or state is None:
            raise ForbiddenError("Сброс есть только у демо-клиники", code="not_demo_clinic")
        doctors = (await self._schedule.practitioner_ids(clinic_id))[:MAX_DOCTORS]
        services = await self._demo.priced_services(clinic_id)
        if not doctors or not services:
            raise ValidationAppError(
                "Чтобы собрать день, клинике нужны хотя бы один врач и одна услуга с ценой",
                code="demo_not_ready",
                extra={"doctors": len(doctors), "services": len(services)},
            )

        purged = await self._demo.purge(
            clinic_id, state.since, state.appointment_ids, state.patient_ids
        )
        patients = await self._add_patients(now, backdate=backdate)
        appointments = await self._add_visits(
            clinic_id, patients, doctors, services, now, backdate=backdate
        )
        state.reset_at = now
        state.patient_ids = [patient.id for patient in patients]
        state.appointment_ids = [appointment.id for appointment in appointments]
        # Одного сигнала хватает: по нему открытые экраны перечитывают приёмы.
        await signal_appointment_updated(
            self._session, clinic_id, appointments[0].id, ["demoReset"]
        )
        await self._session.commit()

        self._remove_files(purged)
        logger.info(
            "demo_clinic_reset",
            clinic_id=str(clinic_id),
            patients=purged.patients,
            appointments=purged.appointments,
            payments=purged.payments,
            conversations=purged.conversations,
        )
        return ResetResult(
            reset_at=now,
            purged=purged,
            patient_ids=state.patient_ids,
            appointment_ids=state.appointment_ids,
        )

    # ── Сборка дня ───────────────────────────────────────────────────────────

    async def _add_patients(self, now: datetime, *, backdate: bool) -> list[Patient]:
        phones = await self._free_phones(len(PEOPLE))
        patients = [
            Patient(
                phone=phone,
                first_name=person.first_name,
                last_name=person.last_name,
                gender=person.gender,
                birth_date=person.birth_date,
                created_at=now - _KNOWN_AGO if backdate else now,
            )
            for person, phone in zip(PEOPLE, phones, strict=True)
        ]
        await self._demo.add_all(patients)
        return patients

    async def _free_phones(self, count: int) -> list[str]:
        """Свободные выдуманные номера: телефон пациента уникален на платформе."""
        free: list[str] = []
        for start in itertools.count(1, _PHONE_PAGE):
            page = [demo_phone(i) for i in range(start, start + _PHONE_PAGE)]
            taken = await self._demo.taken_phones(page)
            free.extend(phone for phone in page if phone not in taken)
            if len(free) >= count:
                break
        return free[:count]

    async def _add_visits(
        self,
        clinic_id: uuid.UUID,
        patients: list[Patient],
        doctors: list[uuid.UUID],
        services: list[Service],
        now: datetime,
        *,
        backdate: bool,
    ) -> list[Appointment]:
        at = anchor(now)
        visits: list[Appointment] = []
        for index, (plan, patient) in enumerate(zip(DAY_PLAN, patients, strict=True)):
            starts_at = visit_starts(plan, at)
            # Дорогая услуга — у приёма с долгом: долг должен быть заметен.
            service = (
                services[0] if plan.payment is Payment.PART else services[index % len(services)]
            )
            visit = Appointment(
                clinic_id=clinic_id,
                patient_id=patient.id,
                doctor_membership_id=await self._doctor_for(plan, doctors, starts_at),
                service_id=service.id,
                scheduled_at=starts_at,
                duration_min=plan.duration_minutes,
                status=plan.status,
                price_total_tiyin=service.price_tiyin if plan.billed else 0,
                created_at=at - _BOOKED_AGO if backdate else now,
            )
            # По одному: следующий приём проверяет занятость врача уже с учётом этого.
            await self._demo.add_all([visit])
            visits.append(visit)
            if plan.billed:
                await self._demo.add_all(self._bill(visit, service, plan, starts_at))
        return visits

    async def _doctor_for(
        self, plan: PlannedVisit, doctors: list[uuid.UUID], starts_at: datetime
    ) -> uuid.UUID | None:
        """Врач по плану; занят прежней записью клиники — свободный, нет — без врача."""
        ends_at = starts_at + timedelta(minutes=plan.duration_minutes)
        for shift in range(len(doctors)):
            doctor = doctors[(plan.doctor + shift) % len(doctors)]
            if not await self._demo.is_doctor_busy(doctor, starts_at, ends_at):
                return doctor
        return None

    @staticmethod
    def _bill(
        visit: Appointment, service: Service, plan: PlannedVisit, starts_at: datetime
    ) -> list[AppointmentItem | CashPayment]:
        done = plan.status is AppointmentStatus.COMPLETED
        rows: list[AppointmentItem | CashPayment] = [
            AppointmentItem(
                appointment_id=visit.id,
                service_id=service.id,
                doctor_membership_id=visit.doctor_membership_id,
                status=AppointmentItemStatus.COMPLETED if done else AppointmentItemStatus.STARTED,
                price_tiyin=service.price_tiyin,
            )
        ]
        amount = {
            Payment.NONE: 0,
            Payment.PART: partial_payment(service.price_tiyin),
            Payment.FULL: service.price_tiyin,
        }[plan.payment]
        if amount > 0:
            rows.append(
                CashPayment(
                    clinic_id=visit.clinic_id,
                    appointment_id=visit.id,
                    patient_id=visit.patient_id,
                    amount_tiyin=amount,
                    method=(
                        PaymentMethod.CARD if plan.payment is Payment.PART else PaymentMethod.CASH
                    ),
                    kind=PaymentKind.PAYMENT,
                    recorded_by_membership_id=visit.doctor_membership_id,
                    created_at=starts_at + timedelta(minutes=plan.duration_minutes),
                )
            )
        return rows

    @staticmethod
    def _remove_files(purged: Purged) -> None:
        """Убрать с диска файлы удалённых строк — после коммита и не роняя сброс."""
        for path in purged.file_paths:
            try:
                resolve_stored_path(path).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("demo_file_remove_failed", path=path, error=str(exc))


def build_demo_reset_service(session: AsyncSession) -> DemoResetService:
    """Собрать сервис на одной сессии — для ручки и для задания воркера."""
    return DemoResetService(session, DemoRepository(session), ScheduleRepository(session))
