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
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.ai.domain.texts import booking_text
from app.modules.appointments.application.use_cases.create_appointment import (
    CreateAppointmentInput,
    CreateAppointmentUseCase,
)
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.push.domain.events import format_som
from app.modules.schedule.application.services.availability_service import (
    build_availability_service,
    span_of,
)
from app.shared.db.enums import AppointmentStatus, AppointmentType, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.service import Service
from app.shared.db.models.slot_hold import SlotHold

logger = structlog.get_logger(__name__)

#: Сколько живёт мягкий резерв слота (§6.4).
HOLD_TTL = timedelta(minutes=10)
#: Префикс id кнопки подтверждения в WhatsApp.
CONFIRM_PREFIX = "hold:"


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
        duration = await availability.resolve_duration(
            conversation.clinic_id, service_id, None
        )
        await availability.ensure_bookable(
            conversation.clinic_id, doctor_membership_id, span_of(starts_at, duration)
        )
        # Чужой живой резерв на этот слот — «двое в одно кресло» (§6.4).
        taken = (
            await self._session.execute(
                select(SlotHold.id).where(
                    SlotHold.doctor_membership_id == doctor_membership_id,
                    SlotHold.starts_at == starts_at,
                    SlotHold.consumed_at.is_(None),
                    SlotHold.expires_at > datetime.now(UTC),
                    SlotHold.conversation_id != conversation.id,
                )
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise AppError(
                "Это время только что предложено другому пациенту — выберите соседнее",
                code="slot_on_hold",
                status_code=409,
            )
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

    async def confirm(self, conversation: AiConversation, raw_button_id: str) -> str:
        """Создать приём по нажатой кнопке; вернуть текст ответа пациенту.

        Идемпотентно: повторное нажатие отвечает «вы уже записаны», второй
        приём не создаётся. Протухший резерв — «предложение больше не
        действует». Гонка за слот разрешается штатной проверкой занятости
        (и EXCLUDE-констрейнтом) — пациенту уходит извинение и просьба выбрать
        другое время (§6.4: эскалация не нужна).
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
        if hold.appointment_id is not None:
            return booking_text("already_booked", lang)
        if hold.consumed_at is not None:
            # Потреблён без приёма — прошлая попытка упёрлась в занятый слот.
            return booking_text("hold_expired", lang)
        now = datetime.now(UTC)
        if hold.expires_at < now:
            return booking_text("hold_expired", lang)

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
            await self._session.execute(
                select(Clinic.timezone).where(Clinic.id == clinic_id)
            )
        ).scalar_one_or_none()
        try:
            return ZoneInfo(name or "Asia/Bishkek")
        except (KeyError, ValueError):
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
        """Ближайшие дни со свободными слотами — для этапа «когда удобно?»."""
        from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
        from app.modules.schedule.infrastructure.repositories.schedule_repository import (
            ScheduleRepository,
        )

        use_case = GetSlotsUseCase(
            ScheduleRepository(self._session), build_availability_service(self._session)
        )
        tz = await self._clinic_tz(clinic_id)
        result: list[dict[str, Any]] = []
        day = date_from.astimezone(tz).date()
        for offset in range(min(days, 7)):
            current = day + timedelta(days=offset)
            try:
                plan = await use_case.execute(
                    clinic_id, doctor_membership_id, current, service_id
                )
            except AppError:
                continue
            if plan.slots:
                result.append(
                    {
                        "date": current.isoformat(),
                        "freeSlots": len(plan.slots),
                        "firstSlot": plan.slots[0].span.start.astimezone(tz).strftime("%H:%M"),
                    }
                )
        return result


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
