"""Запись через ассистента: мягкий резерв → кнопка → подтверждение (§6.2–6.4).

Центральная гарантия: модель может только ПРЕДЛОЖИТЬ запись по идентификаторам
из выдачи инструментов; приём создаёт обработчик кнопки подтверждения — тем же
``CreateAppointmentUseCase``, что и запись из приложения клиники (те же
проверки занятости, EXCLUDE-констрейнт, длительность по зубам, события
``appointment.created`` → инбокс и пуш врачу).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.modules.ai.domain.texts import booking_text
from app.modules.appointments.application.use_cases.create_appointment import (
    CreateAppointmentInput,
    CreateAppointmentUseCase,
)
from app.modules.appointments.application.use_cases.update_appointment import (
    UpdateAppointmentUseCase,
)
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.infrastructure.repositories.audit_repository import AuditRepository
from app.modules.push.domain.events import format_som
from app.modules.schedule.application.services.availability_service import (
    build_availability_service,
    span_of,
)
from app.shared.db.enums import AppointmentStatus, AppointmentType, CancelReason, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.service import Service
from app.shared.db.models.slot_hold import SlotHold

logger = structlog.get_logger(__name__)

#: Сколько живёт мягкий резерв слота (§6.4).
HOLD_TTL = timedelta(minutes=10)
#: Префикс id кнопки подтверждения в WhatsApp.
CONFIRM_PREFIX = "hold:"
#: Виды предложения (Ф7): новая запись, перенос, отмена — одна кнопка.
HOLD_BOOK = "book"
HOLD_RESCHEDULE = "reschedule"
HOLD_CANCEL = "cancel"
CHANGEABLE_STATUSES = (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED)


@dataclass(frozen=True, slots=True)
class ProposeResult:
    """Что вернуть модели после резерва."""

    confirmation_id: str
    card: dict[str, Any]


class BookingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Предложение (зовёт инструмент модели) ───────────────────────────────

    async def propose(
        self,
        conversation: AiConversation,
        *,
        doctor_membership_id: uuid.UUID,
        starts_at: datetime,
        service_id: uuid.UUID | None,
    ) -> ProposeResult:
        """Мягко зарезервировать слот и собрать карточку подтверждения.

        Слот перепроверяется здесь же (`ensure_bookable`): модель могла держать
        устаревшую выдачу. Ошибки уходят наружу :class:`AppError` — executor
        вернёт их модели данными, и она предложит другое время.
        """
        availability = build_availability_service(self._session)
        if not await availability.is_doctor(conversation.clinic_id, doctor_membership_id):
            raise AppError(
                "Такого врача нет — возьми doctorId из выдачи get_doctors",
                code="invalid_doctor",
                status_code=422,
            )
        duration = await availability.resolve_duration(conversation.clinic_id, service_id, None)
        await availability.ensure_bookable(
            conversation.clinic_id, doctor_membership_id, span_of(starts_at, duration)
        )
        await self._ensure_not_held(conversation, doctor_membership_id, starts_at)
        if conversation.patient_id is None:
            raise AppError(
                "Сначала узнайте имя пациента и вызовите create_patient",
                code="patient_required",
                status_code=422,
            )

        doctor_name, service_name, price_som = await self._card_parts(
            doctor_membership_id, service_id
        )
        tz_time = starts_at.astimezone(UTC)  # локализует карточка ниже
        summary_bits = [service_name or "Приём", f"врач {doctor_name}"]
        if price_som:
            summary_bits.append(f"{price_som} сом")
        summary = ", ".join(summary_bits)

        hold = SlotHold(
            conversation_id=conversation.id,
            clinic_id=conversation.clinic_id,
            patient_id=conversation.patient_id,
            doctor_membership_id=doctor_membership_id,
            service_id=service_id,
            starts_at=starts_at,
            duration_min=duration,
            summary=summary,
            expires_at=datetime.now(UTC) + HOLD_TTL,
        )
        self._session.add(hold)
        await self._session.flush()
        return ProposeResult(
            confirmation_id=f"{CONFIRM_PREFIX}{hold.id}",
            card={
                "confirmationId": f"{CONFIRM_PREFIX}{hold.id}",
                "doctor": doctor_name,
                "service": service_name,
                "startsAt": tz_time.isoformat(),
                "durationMin": duration,
                "priceSom": price_som,
                "holdMinutes": int(HOLD_TTL.total_seconds() // 60),
            },
        )

    # ── Перенос и отмена (Ф7): та же кнопка, тот же use-case правки ─────────

    async def propose_reschedule(
        self,
        conversation: AiConversation,
        *,
        appointment_id: uuid.UUID,
        starts_at: datetime,
        doctor_membership_id: uuid.UUID | None,
    ) -> ProposeResult:
        """Предложить перенос своей будущей записи: резерв нового слота + кнопка."""
        appointment = await self._own_changeable(conversation, appointment_id)
        doctor_id = doctor_membership_id or appointment.doctor_membership_id
        if doctor_id is None:
            raise AppError(
                "У записи нет врача — выберите врача через get_doctors",
                code="doctor_required",
                status_code=422,
            )
        availability = build_availability_service(self._session)
        if doctor_membership_id is not None and not await availability.is_doctor(
            conversation.clinic_id, doctor_membership_id
        ):
            raise AppError(
                "Такого врача нет — возьми doctorId из выдачи get_doctors",
                code="invalid_doctor",
                status_code=422,
            )
        duration = appointment.duration_min
        await availability.ensure_bookable(
            conversation.clinic_id,
            doctor_id,
            span_of(starts_at, duration),
            exclude_appointment_id=appointment.id,
        )
        await self._ensure_not_held(conversation, doctor_id, starts_at)
        doctor_name, service_name, _ = await self._card_parts(doctor_id, appointment.service_id)
        summary = ", ".join([service_name or "Приём", f"врач {doctor_name}"])
        hold = SlotHold(
            kind=HOLD_RESCHEDULE,
            target_appointment_id=appointment.id,
            conversation_id=conversation.id,
            clinic_id=conversation.clinic_id,
            patient_id=appointment.patient_id,
            doctor_membership_id=doctor_id,
            service_id=appointment.service_id,
            starts_at=starts_at,
            duration_min=duration,
            summary=summary,
            expires_at=datetime.now(UTC) + HOLD_TTL,
        )
        self._session.add(hold)
        await self._session.flush()
        return ProposeResult(
            confirmation_id=f"{CONFIRM_PREFIX}{hold.id}",
            card={
                "confirmationId": f"{CONFIRM_PREFIX}{hold.id}",
                "kind": HOLD_RESCHEDULE,
                "appointmentId": str(appointment.id),
                "doctor": doctor_name,
                "service": service_name,
                "fromStartsAt": (
                    appointment.scheduled_at.isoformat() if appointment.scheduled_at else None
                ),
                "startsAt": starts_at.astimezone(UTC).isoformat(),
                "durationMin": duration,
                "holdMinutes": int(HOLD_TTL.total_seconds() // 60),
            },
        )

    async def propose_cancel(
        self, conversation: AiConversation, *, appointment_id: uuid.UUID
    ) -> ProposeResult:
        """Предложить отмену своей будущей записи: кнопка подтверждения отмены."""
        appointment = await self._own_changeable(conversation, appointment_id)
        if appointment.doctor_membership_id is None or appointment.scheduled_at is None:
            raise AppError(
                "У записи нет врача или времени — передайте её администратору",
                code="appointment_incomplete",
                status_code=409,
            )
        doctor_name, service_name, _ = await self._card_parts(
            appointment.doctor_membership_id, appointment.service_id
        )
        summary = ", ".join([service_name or "Приём", f"врач {doctor_name}"])
        hold = SlotHold(
            kind=HOLD_CANCEL,
            target_appointment_id=appointment.id,
            conversation_id=conversation.id,
            clinic_id=conversation.clinic_id,
            patient_id=appointment.patient_id,
            doctor_membership_id=appointment.doctor_membership_id,
            service_id=appointment.service_id,
            starts_at=appointment.scheduled_at,
            duration_min=appointment.duration_min,
            summary=summary,
            expires_at=datetime.now(UTC) + HOLD_TTL,
        )
        self._session.add(hold)
        await self._session.flush()
        return ProposeResult(
            confirmation_id=f"{CONFIRM_PREFIX}{hold.id}",
            card={
                "confirmationId": f"{CONFIRM_PREFIX}{hold.id}",
                "kind": HOLD_CANCEL,
                "appointmentId": str(appointment.id),
                "doctor": doctor_name,
                "service": service_name,
                "startsAt": appointment.scheduled_at.astimezone(UTC).isoformat(),
                "holdMinutes": int(HOLD_TTL.total_seconds() // 60),
            },
        )

    async def _own_changeable(
        self, conversation: AiConversation, appointment_id: uuid.UUID
    ) -> Appointment:
        """Запись пациента этого диалога, будущая и ещё не начатая — иначе отказ данными."""
        if conversation.patient_id is None:
            raise AppError(
                "Сначала узнайте имя пациента и вызовите create_patient",
                code="patient_required",
                status_code=422,
            )
        appointment = await self._session.get(Appointment, appointment_id)
        if (
            appointment is None
            or appointment.clinic_id != conversation.clinic_id
            or appointment.patient_id != conversation.patient_id
        ):
            raise AppError(
                "Такой записи у пациента нет — возьми appointmentId из get_patient_appointments",
                code="not_your_appointment",
                status_code=404,
            )
        if appointment.status not in CHANGEABLE_STATUSES:
            raise AppError(
                f"Запись в статусе {appointment.status.value} изменить нельзя — "
                "передайте администратору",
                code="appointment_not_changeable",
                status_code=409,
            )
        if appointment.scheduled_at is None or appointment.scheduled_at <= datetime.now(UTC):
            raise AppError(
                "Эта запись уже прошла — предложите новую",
                code="appointment_in_past",
                status_code=409,
            )
        return appointment

    async def _ensure_not_held(
        self, conversation: AiConversation, doctor_membership_id: uuid.UUID, starts_at: datetime
    ) -> None:
        """Чужой живой резерв на этот слот — «двое в одно кресло» (§6.4)."""
        taken = (
            await self._session.execute(
                select(SlotHold.id).where(
                    SlotHold.doctor_membership_id == doctor_membership_id,
                    SlotHold.starts_at == starts_at,
                    SlotHold.consumed_at.is_(None),
                    SlotHold.expires_at > datetime.now(UTC),
                    SlotHold.conversation_id != conversation.id,
                    SlotHold.kind != HOLD_CANCEL,  # отмена слот не держит
                )
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise AppError(
                "Это время только что предложено другому пациенту — выберите соседнее",
                code="slot_on_hold",
                status_code=409,
            )

    async def _confirm_change(self, hold: SlotHold, lang: str, *, dry_run: bool = False) -> str:
        """Перенос или отмена по кнопке — штатным use-case правки приёма.

        Идемпотентно, как и запись: повтор кнопки отвечает «уже выполнено».
        Актор аудита — не сотрудник: ``actor_id=None``, ``via="assistant"``.
        """
        is_reschedule = hold.kind == HOLD_RESCHEDULE
        if hold.appointment_id is not None:
            return booking_text(
                "already_rescheduled" if is_reschedule else "already_cancelled", lang
            )
        if hold.consumed_at is not None:
            return booking_text("hold_expired", lang)
        now = datetime.now(UTC)
        if hold.expires_at < now:
            return booking_text("hold_expired", lang)
        target_id = hold.target_appointment_id
        appointment = (
            await self._session.get(Appointment, target_id) if target_id is not None else None
        )
        if (
            target_id is None
            or appointment is None
            or appointment.status not in CHANGEABLE_STATUSES
            or appointment.scheduled_at is None
            or appointment.scheduled_at <= now
        ):
            # Пока пациент думал, запись изменилась (пришёл, отменили, прошла):
            # кнопка не должна ни отменять визит, ни двигать историю.
            if not dry_run:
                hold.consumed_at = now
                await self._session.commit()
            return booking_text("appointment_gone", lang)
        if dry_run:
            # Пробный прогон: текст как при настоящем нажатии, запись не трогаем.
            local = hold.starts_at.astimezone(await self._clinic_tz(hold.clinic_id))
            return booking_text("rescheduled" if is_reschedule else "cancelled", lang).format(
                summary=hold.summary, when=local.strftime("%d.%m %H:%M")
            )

        hold_id, clinic_id = hold.id, hold.clinic_id
        starts_at, summary = hold.starts_at, hold.summary
        hold.consumed_at = now  # пометка ДО use-case: его commit фиксирует всё атомарно
        use_case = UpdateAppointmentUseCase(
            self._session,
            AppointmentRepository(self._session),
            build_availability_service(self._session),
            AuditService(self._session, AuditRepository(self._session)),
        )
        fields: dict[str, Any]
        if is_reschedule:
            fields = {"scheduled_at": starts_at, "doctor_membership_id": hold.doctor_membership_id}
        else:
            fields = {
                "status": AppointmentStatus.CANCELLED,
                "cancel_reason": CancelReason.PATIENT_REQUEST,
                "cancel_comment": "Отмена пациентом через WhatsApp-ассистента",
            }
        try:
            await use_case.execute(clinic_id, target_id, fields, actor_id=None, via="assistant")
        except AppError as exc:
            await self._session.rollback()
            await self._session.execute(
                update(SlotHold)
                .where(SlotHold.id == hold_id, SlotHold.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            await self._session.commit()
            logger.info("ai_booking_change_failed", hold_id=str(hold_id), error=exc.code)
            if is_reschedule and exc.status_code == 409:
                return booking_text("slot_taken", lang)
            return booking_text("appointment_gone", lang)
        await self._session.execute(
            update(SlotHold).where(SlotHold.id == hold_id).values(appointment_id=target_id)
        )
        await self._session.commit()
        local = starts_at.astimezone(await self._clinic_tz(clinic_id))
        return booking_text("rescheduled" if is_reschedule else "cancelled", lang).format(
            summary=summary, when=local.strftime("%d.%m %H:%M")
        )

    async def _card_parts(
        self, doctor_membership_id: uuid.UUID, service_id: uuid.UUID | None
    ) -> tuple[str, str | None, str | None]:
        doctor_name = (
            await self._session.execute(
                select(Account.full_name)
                .join(Membership, Membership.account_id == Account.id)
                .where(Membership.id == doctor_membership_id)
            )
        ).scalar_one_or_none() or "врач"
        service_name: str | None = None
        price_som: str | None = None
        if service_id is not None:
            row = (
                await self._session.execute(
                    select(Service.name, Service.price_tiyin).where(Service.id == service_id)
                )
            ).first()
            if row is not None:
                service_name = row[0]
                price_som = format_som(row[1])
        return doctor_name, service_name, price_som

    # ── Подтверждение (зовёт ТОЛЬКО обработчик кнопки, не модель) ───────────

    async def confirm(
        self, conversation: AiConversation, raw_button_id: str, *, dry_run: bool = False
    ) -> str:
        """Создать приём по нажатой кнопке; вернуть текст ответа пациенту.

        Идемпотентно: повторное нажатие отвечает «вы уже записаны», второй
        приём не создаётся. Протухший резерв — «предложение больше не
        действует». Гонка за слот разрешается штатной проверкой занятости
        (и EXCLUDE-констрейнтом) — пациенту уходит извинение и просьба выбрать
        другое время (§6.4: эскалация не нужна).

        ``dry_run`` — пробный прогон симулятора: проверки и текст те же, но
        приём не создаётся, а резерв не гасится.
        """
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        try:
            hold_id = uuid.UUID(raw_button_id.removeprefix(CONFIRM_PREFIX))
        except ValueError:
            return booking_text("hold_missing", lang)
        hold = (
            await self._session.execute(
                select(SlotHold)
                .where(
                    SlotHold.id == hold_id,
                    SlotHold.conversation_id == conversation.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if hold is None:
            return booking_text("hold_missing", lang)
        if hold.kind != HOLD_BOOK:
            return await self._confirm_change(hold, lang, dry_run=dry_run)
        if hold.appointment_id is not None:
            return booking_text("already_booked", lang)
        if hold.consumed_at is not None:
            # Потреблён без приёма — прошлая попытка упёрлась в занятый слот.
            return booking_text("hold_expired", lang)
        now = datetime.now(UTC)
        if hold.expires_at < now:
            return booking_text("hold_expired", lang)
        if dry_run:
            # Пробный прогон: пациент увидел бы «вы записаны», но приёма нет и
            # резерв остаётся живым — настоящее нажатие сработает как обычно.
            local = hold.starts_at.astimezone(await self._clinic_tz(hold.clinic_id))
            return booking_text("booked", lang).format(
                summary=hold.summary, when=local.strftime("%d.%m %H:%M")
            )

        # Пометка ДО use-case: его commit фиксирует резерв и приём атомарно.
        hold.consumed_at = now
        use_case = CreateAppointmentUseCase(
            self._session,
            AppointmentRepository(self._session),
            build_availability_service(self._session),
        )
        items = []
        if hold.service_id is not None:
            from app.modules.appointments.domain.models import ItemInput as ApptItem

            items = [
                ApptItem(
                    service_id=hold.service_id,
                    price_tiyin=None,
                    quantity=1,
                    teeth=[],
                    comment=None,
                )
            ]
        try:
            appointment = await use_case.execute(
                hold.clinic_id,
                CreateAppointmentInput(
                    patient_id=hold.patient_id,
                    doctor_membership_id=hold.doctor_membership_id,
                    service_id=hold.service_id,
                    appointment_type=AppointmentType.TREATMENT,
                    scheduled_at=hold.starts_at,
                    duration_min=hold.duration_min,
                    comment="Запись через WhatsApp-ассистента",
                    items=items,
                    status=AppointmentStatus.SCHEDULED,
                ),
            )
        except AppError as exc:
            # Слот успели занять: извинение и новый подбор — без эскалации.
            # Резерв гасим отдельным коммитом, чтобы повтор кнопки не молотил
            # use-case заново (rollback снял бы и пометку consumed_at).
            await self._session.rollback()
            await self._session.execute(
                update(SlotHold)
                .where(SlotHold.id == hold_id, SlotHold.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            await self._session.commit()
            logger.info("ai_booking_slot_lost", hold_id=str(hold_id), error=exc.code)
            return booking_text("slot_taken", lang)
        hold.appointment_id = appointment.id
        await self._session.commit()

        local = hold.starts_at.astimezone(await self._clinic_tz(hold.clinic_id))
        return booking_text("booked", lang).format(
            summary=hold.summary, when=local.strftime("%d.%m %H:%M")
        )

    async def _clinic_tz(self, clinic_id: uuid.UUID) -> Any:
        from zoneinfo import ZoneInfo

        name = (
            await self._session.execute(select(Clinic.timezone).where(Clinic.id == clinic_id))
        ).scalar_one_or_none()
        try:
            return ZoneInfo(name or "Asia/Bishkek")
        except KeyError, ValueError:
            return ZoneInfo("Asia/Bishkek")

    async def free_days(
        self,
        clinic_id: uuid.UUID,
        *,
        date_from: datetime,
        days: int,
        doctor_membership_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        """Ближайшие дни со свободными слотами — для этапа «когда удобно?».

        Прошедшее время свободным не считается: сегодня в 14:35 слоты на утро
        уже недоступны. Без врача и услуги ищем по всем врачам клиники — иначе
        правило подбора слотов («нужен врач или услуга») давало пустой список,
        и бот говорил, что свободных дней нет.
        """
        use_case = self._slots_use_case()
        tz = await self._clinic_tz(clinic_id)
        now = datetime.now(UTC)
        not_before = earliest_bookable(now)
        doctors = await self._doctors_to_search(clinic_id, doctor_membership_id, service_id)
        result: list[dict[str, Any]] = []
        day = max(date_from, now).astimezone(tz).date()
        for offset in range(min(days, 7)):
            current = day + timedelta(days=offset)
            starts = await self._day_starts(
                use_case, clinic_id, doctors, current, service_id, not_before=not_before
            )
            if starts:
                result.append(
                    {
                        "date": current.isoformat(),
                        "freeSlots": len(starts),
                        "firstSlot": starts[0].astimezone(tz).strftime("%H:%M"),
                    }
                )
        return result

    async def earliest_slots(
        self, clinic_id: uuid.UUID, *, days: int = 3, limit: int = 3
    ) -> list[datetime]:
        """Самые ранние свободные начала приёма (время клиники) — по всем врачам.

        Для шаблонов медфильтра: у них нет ни врача, ни услуги. Берётся первый
        день, где есть свободное время, — сегодня, если успеваем, — и до
        ``limit`` самых ранних начал в нём. Пусто — свободного времени нет.
        """
        use_case = self._slots_use_case()
        tz = await self._clinic_tz(clinic_id)
        now = datetime.now(UTC)
        not_before = earliest_bookable(now)
        doctors = await self._doctors_to_search(clinic_id, None, None)
        today = now.astimezone(tz).date()
        for offset in range(days):
            starts = await self._day_starts(
                use_case,
                clinic_id,
                doctors,
                today + timedelta(days=offset),
                None,
                not_before=not_before,
            )
            if starts:
                return [start.astimezone(tz) for start in starts[:limit]]
        return []

    def _slots_use_case(self) -> Any:
        from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
        from app.modules.schedule.infrastructure.repositories.schedule_repository import (
            ScheduleRepository,
        )

        return GetSlotsUseCase(
            ScheduleRepository(self._session), build_availability_service(self._session)
        )

    async def _doctors_to_search(
        self,
        clinic_id: uuid.UUID,
        doctor_membership_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
    ) -> list[uuid.UUID | None]:
        """Кого перебирать: указанного врача, исполнителей услуги или всех врачей."""
        if doctor_membership_id is not None or service_id is not None:
            # Услугу без врача use-case подбирает сам — по её исполнителям.
            return [doctor_membership_id]
        rows = (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Membership.specialization_id.is_not(None),
                )
            )
        ).scalars()
        return list(rows)

    @staticmethod
    async def _day_starts(
        use_case: Any,
        clinic_id: uuid.UUID,
        doctors: list[uuid.UUID | None],
        day: date,
        service_id: uuid.UUID | None,
        *,
        not_before: datetime,
    ) -> list[datetime]:
        """Свободные начала приёма в день по всем ``doctors`` — не раньше ``not_before``."""
        starts: set[datetime] = set()
        for doctor_id in doctors:
            try:
                plan = await use_case.execute(clinic_id, doctor_id, day, service_id)
            except AppError:
                continue
            starts.update(slot.span.start for slot in plan.slots if slot.span.start >= not_before)
        return sorted(starts)


def earliest_bookable(now: datetime) -> datetime:
    """С какого момента ассистент предлагает время: сейчас + ``AI_SLOT_LEAD_MINUTES``.

    Слот, начинающийся через минуту, формально свободен, но пациенту до него не
    добраться — предлагать такое значит записать на неявку.
    """
    return now + timedelta(minutes=settings.ai_slot_lead_minutes)


async def hold_count_alive(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    """Живые резервы диалога (для симулятора/отладки)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(SlotHold)
            .where(
                SlotHold.conversation_id == conversation_id,
                SlotHold.consumed_at.is_(None),
                SlotHold.expires_at > datetime.now(UTC),
            )
        )
    ).scalar_one()
