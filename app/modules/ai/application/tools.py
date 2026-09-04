"""Инструменты ассистента (§6.1) — только чтение и предложения.

Красная зона: цены, слоты, врачи приходят ТОЛЬКО отсюда, из живой БД. Каждый
вызов журналируется в ``ai_tool_call`` — по нему валидатор сверит факты, а при
споре видно, откуда взялось каждое число. Инструментов записи здесь нет:
``propose_*``/``confirm_action`` — следующий инкремент.
"""

from __future__ import annotations

import time as time_mod
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.application.booking import BookingService
from app.modules.ai.domain.texts import booking_text
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.patients.infrastructure.repositories.patient_repository import (
    PatientRepository,
)
from app.modules.push.domain.events import format_som
from app.modules.schedule.application.services.availability_service import (
    build_availability_service,
)
from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
from app.modules.schedule.infrastructure.repositories.schedule_repository import (
    ScheduleRepository,
)
from app.shared.ai import AiToolDecl
from app.shared.db.enums import AiMode, AppointmentStatus, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.service import Service
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

#: Декларации для модели. Описания — на русском: их читает модель, не пациент.
TOOL_DECLS: list[AiToolDecl] = [
    AiToolDecl(
        name="get_services",
        description="Прайс клиники: услуги с ценами в сомах. query — фильтр по названию.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "часть названия"}},
        },
    ),
    AiToolDecl(
        name="get_doctors",
        description="Врачи клиники со специализациями.",
        parameters={"type": "object", "properties": {}},
    ),
    AiToolDecl(
        name="get_available_slots",
        description=(
            "Свободное время записи на дату (YYYY-MM-DD). Передай doctorId "
            "и/или serviceId, если известны."
        ),
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "doctorId": {"type": "string"},
                "serviceId": {"type": "string"},
            },
            "required": ["date"],
        },
    ),
    AiToolDecl(
        name="get_patient_appointments",
        description="Записи пациента этого диалога (прошлые и будущие).",
        parameters={"type": "object", "properties": {}},
    ),
    AiToolDecl(
        name="get_available_days",
        description=(
            "Ближайшие дни со свободными слотами — для вопроса «когда удобно?». "
            "dateFrom (YYYY-MM-DD, по умолчанию сегодня), days ≤ 7."
        ),
        parameters={
            "type": "object",
            "properties": {
                "dateFrom": {"type": "string", "description": "YYYY-MM-DD"},
                "days": {"type": "integer", "description": "сколько дней проверить, ≤7"},
                "doctorId": {"type": "string"},
                "serviceId": {"type": "string"},
            },
        },
    ),
    AiToolDecl(
        name="create_patient",
        description=(
            "Завести карточку пациента этого диалога (номер берётся из чата). "
            "Звать, когда пациент НЕ найден по номеру и назвал своё имя. Имя — "
            "ДОСЛОВНО как написал пациент, без исправлений и дополнений."
        ),
        parameters={
            "type": "object",
            "properties": {
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
            },
            "required": ["firstName"],
        },
    ),
    AiToolDecl(
        name="propose_appointment",
        description=(
            "Зарезервировать выбранный слот на 10 минут и отправить пациенту "
            "кнопку подтверждения. startsAt — ровно из выдачи "
            "get_available_slots. Запись создаётся ТОЛЬКО после кнопки — "
            "не обещай, что уже записал."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doctorId": {"type": "string"},
                "startsAt": {"type": "string", "description": "ISO из get_available_slots"},
                "serviceId": {"type": "string"},
            },
            "required": ["doctorId", "startsAt"],
        },
    ),
    AiToolDecl(
        name="suggest_glossary_term",
        description=(
            "Записать подтверждённое пациентом значение непонятной фразы в "
            "очередь глоссария (администратор одобрит). Звать ТОЛЬКО после "
            "явного подтверждения собеседника."
        ),
        parameters={
            "type": "object",
            "properties": {
                "phrase": {"type": "string", "description": "фраза как её написал пациент"},
                "meaning": {"type": "string", "description": "подтверждённое значение"},
                "lang": {"type": "string", "description": "ru | ky | mixed"},
            },
            "required": ["phrase", "meaning"],
        },
    ),
    AiToolDecl(
        name="escalate",
        description=(
            "Позвать человека. reason: unknown — в базе знаний нет ответа на вопрос "
            "(клиника его выучит), patient_asked — пациент просит человека, "
            "policy — вопрос вне твоих правил."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "note": {"type": "string", "description": "что передать администратору"},
            },
            "required": ["reason"],
        },
    ),
]


@dataclass(slots=True)
class ToolContext:
    """Что нужно инструментам: диалог, клиника, канал отправки кнопок."""

    conversation: AiConversation
    clinic: Clinic
    wa_client: WhatsAppCloudClient
    binding_phone_number_id: str | None

    @property
    def clinic_id(self) -> uuid.UUID:
        return self.conversation.clinic_id

    @property
    def patient_id(self) -> uuid.UUID | None:
        return self.conversation.patient_id


class ToolExecutor:
    """Исполняет вызовы модели против БД и журналирует их."""

    def __init__(self, session: AsyncSession, repository: AiRepository) -> None:
        self._session = session
        self._repository = repository

    async def execute(
        self, conversation_id: uuid.UUID, context: ToolContext, name: str, args: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        """Выполнить инструмент; ошибка уходит модели текстом, не исключением.

        Модель обязана уметь жить с отказом инструмента (§8.1 ``tool_error``),
        а прерывать весь ход из-за одного вызова — терять диалог.
        """
        started = time_mod.monotonic()
        error: str | None = None
        result: dict[str, Any] | list[Any]
        try:
            result = await self._dispatch(context, name, args)
        except Exception as exc:  # инструмент не должен ронять ход
            error = str(exc)[:300]
            result = {"error": error}
        self._repository.record_tool_call(
            conversation_id,
            context.clinic_id,
            tool=name,
            args=args,
            result=result,
            error=error,
            duration_ms=int((time_mod.monotonic() - started) * 1000),
        )
        return result

    async def _dispatch(
        self, context: ToolContext, name: str, args: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        if name == "get_services":
            return await self._get_services(context, str(args.get("query") or ""))
        if name == "get_doctors":
            return await self._get_doctors(context)
        if name == "get_available_slots":
            return await self._get_slots(context, args)
        if name == "get_patient_appointments":
            return await self._get_patient_appointments(context)
        if name == "suggest_glossary_term":
            created = await self._repository.suggest_term(
                context.clinic_id,
                str(args.get("phrase") or ""),
                str(args.get("meaning") or ""),
                (str(args["lang"]) if args.get("lang") else None),
            )
            return {"ok": True, "queued": created}
        if name == "get_available_days":
            return await self._get_days(context, args)
        if name == "create_patient":
            return await self._create_patient(context, args)
        if name == "propose_appointment":
            return await self._propose(context, args)
        if name == "escalate":
            # Само действие делает reply-сервис; инструмент лишь подтверждает.
            return {"ok": True}
        return {"error": f"незнакомый инструмент {name}"}

    async def _get_days(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        booking = BookingService(self._session)
        date_from = (
            datetime.fromisoformat(str(args["dateFrom"])).replace(tzinfo=UTC)
            if args.get("dateFrom")
            else datetime.now(UTC)
        )
        days = await booking.free_days(
            context.clinic_id,
            date_from=date_from,
            days=int(args.get("days") or 5),
            doctor_membership_id=(
                uuid.UUID(str(args["doctorId"])) if args.get("doctorId") else None
            ),
            service_id=uuid.UUID(str(args["serviceId"])) if args.get("serviceId") else None,
        )
        if not days:
            return {"days": [], "hint": "свободных дней не нашлось — предложи needs_human"}
        return {"days": days}

    async def _create_patient(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        """Карточка пациента диалога: имя ДОСЛОВНО (§6.5), номер — из чата."""
        first_name = str(args.get("firstName") or "").strip()
        last_name = str(args["lastName"]).strip() if args.get("lastName") else None
        if not first_name:
            return {"error": "нужно имя пациента"}
        patients = PatientRepository(self._session)
        phone = context.conversation.wa_phone
        existing = await patients.find_by_phone(phone)
        if existing is not None:
            # §6.5 «чужой телефон»: пишут с номера мужа или дочери. Угадывать
            # нельзя — запись уйдёт не тому, поэтому эскалация, а не догадка.
            if not _same_person(first_name, existing.first_name):
                return {
                    "error": (
                        "номер записан на другого человека — уточните у "
                        "администратора, не записывайте"
                    ),
                    "_escalate": "identity_mismatch",
                }
            context.conversation.patient_id = existing.id
            return {"patientId": str(existing.id), "existing": True}

        patient = await patients.create(
            phone=phone,
            first_name=first_name,
            last_name=last_name,
            middle_name=None,
            gender=None,
            birth_date=None,
            email=None,
            source="assistant",
        )
        context.conversation.patient_id = patient.id
        await self._session.flush()
        duplicate = await self._possible_duplicate(context, patient, first_name, last_name)
        return {
            "patientId": str(patient.id),
            "existing": False,
            # Пометку разбирает администратор: карточки ассистент не объединяет.
            "possibleDuplicate": duplicate,
        }

    async def _possible_duplicate(
        self,
        context: ToolContext,
        created: Patient,
        first_name: str,
        last_name: str | None,
    ) -> bool:
        """Тот же человек уже лечился здесь под другим номером? (§6.5)

        Сравниваем по имени, а не по похожести номера: смысл проверки ровно в
        том, что вернувшийся пациент пишет с ДРУГОГО телефона — на схожесть
        номеров тут рассчитывать не на что.
        """
        stmt = (
            select(Patient.id)
            .join(Appointment, Appointment.patient_id == Patient.id)
            .where(
                Appointment.clinic_id == context.clinic_id,
                Patient.id != created.id,
                func.lower(Patient.first_name) == first_name.lower(),
            )
            .limit(1)
        )
        if last_name:
            stmt = stmt.where(func.lower(Patient.last_name) == last_name.lower())
        return (await self._session.execute(stmt)).first() is not None

    async def _propose(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        booking = BookingService(self._session)
        starts_at = datetime.fromisoformat(str(args["startsAt"]))
        if starts_at.tzinfo is None:
            return {"error": "startsAt без таймзоны — возьми значение из get_available_slots"}
        result = await booking.propose(
            context.conversation,
            doctor_membership_id=uuid.UUID(str(args["doctorId"])),
            starts_at=starts_at,
            service_id=uuid.UUID(str(args["serviceId"])) if args.get("serviceId") else None,
        )
        # Кнопка уходит пациенту только в auto: в shadow пациент бота не видит.
        button_sent = False
        if context.clinic.ai_mode is AiMode.AUTO and context.binding_phone_number_id is not None:
            lang = "ky" if context.conversation.lang is Lang.KG else "ru"
            local = starts_at.astimezone(self._clinic_tz(context))
            body = booking_text("confirm_button", lang).format(
                summary=result.card.get("service") or "Приём",
                when=local.strftime("%d.%m %H:%M"),
            )
            try:
                await context.wa_client.send_confirm_button(
                    context.binding_phone_number_id,
                    context.conversation.wa_phone,
                    body,
                    result.confirmation_id,
                    booking_text("confirm_title", lang),
                )
                button_sent = True
            except WhatsAppSendError as exc:
                return {"error": f"кнопка не отправилась: {exc}"}
        card = {
            **result.card,
            "startsAt": starts_at.astimezone(self._clinic_tz(context)).isoformat(),
        }
        return {**card, "buttonSent": button_sent}

    async def _get_services(self, context: ToolContext, query: str) -> list[Any]:
        stmt = select(Service).where(
            Service.clinic_id == context.clinic_id, Service.active.is_(True)
        )
        if query:
            stmt = stmt.where(Service.name.ilike(f"%{query}%"))
        rows = (await self._session.execute(stmt.order_by(Service.name).limit(30))).scalars()
        return [
            {
                "serviceId": str(s.id),
                "name": s.name,
                "priceSom": format_som(s.price_tiyin),
                "durationMin": s.duration,
                "fact": f"service:{s.id}:price:{format_som(s.price_tiyin)}",
            }
            for s in rows
        ]

    async def _get_doctors(self, context: ToolContext) -> list[Any]:
        rows = (
            await self._session.execute(
                select(
                    Membership.id,
                    Account.full_name,
                    SpecializationTranslation.value,
                )
                .join(Account, Account.id == Membership.account_id)
                .outerjoin(
                    SpecializationTranslation,
                    (SpecializationTranslation.specialization_id == Membership.specialization_id)
                    & (SpecializationTranslation.lang == Lang.RU),
                )
                .where(
                    Membership.clinic_id == context.clinic_id,
                    Membership.active.is_(True),
                    Membership.specialization_id.is_not(None),
                )
                .order_by(Account.full_name)
            )
        ).all()
        return [
            {
                "doctorId": str(r[0]),
                "name": r[1],
                "specialization": r[2],
                "fact": f"doctor:{r[0]}",
            }
            for r in rows
        ]

    @staticmethod
    def _clinic_tz(context: ToolContext) -> ZoneInfo:
        """Зона клиники: время пациенту называют местное, а не UTC."""
        try:
            return ZoneInfo(context.clinic.timezone or "Asia/Bishkek")
        except KeyError, ValueError:
            return ZoneInfo("Asia/Bishkek")

    async def _get_slots(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        day = date.fromisoformat(str(args.get("date")))
        doctor_id = uuid.UUID(str(args["doctorId"])) if args.get("doctorId") else None
        service_id = uuid.UUID(str(args["serviceId"])) if args.get("serviceId") else None
        schedule = ScheduleRepository(self._session)
        use_case = GetSlotsUseCase(schedule, build_availability_service(self._session))
        if doctor_id is None and service_id is None:
            # Правило use-case'а: без врача и услуги подбирать не по чему.
            doctors = await self._get_doctors(context)
            return {"error": "нужен doctorId или serviceId", "doctors": doctors}
        plan = await use_case.execute(context.clinic_id, doctor_id, day, service_id)
        # Наружу — местное время клиники: модель называет слот пациенту дословно,
        # и UTC в её выдаче означал бы приглашение прийти на несколько часов раньше.
        tz = self._clinic_tz(context)
        slots = [
            {
                "slot": s.span.start.astimezone(tz).isoformat(),
                "doctorId": str(s.doctor_membership_id),
                "fact": f"slot:{s.doctor_membership_id}:{s.span.start.isoformat()}",
            }
            for s in plan.slots[:10]
        ]
        return {"date": day.isoformat(), "durationMin": plan.duration_min, "slots": slots}

    async def _get_patient_appointments(self, context: ToolContext) -> list[Any]:
        if context.patient_id is None:
            return []
        rows = (
            await self._session.execute(
                select(
                    Appointment.id,
                    Appointment.scheduled_at,
                    Appointment.status,
                    func.concat_ws(" ", Account.full_name),
                )
                .outerjoin(Membership, Membership.id == Appointment.doctor_membership_id)
                .outerjoin(Account, Account.id == Membership.account_id)
                .where(
                    Appointment.clinic_id == context.clinic_id,
                    Appointment.patient_id == context.patient_id,
                    Appointment.status != AppointmentStatus.DRAFT,
                )
                .order_by(Appointment.scheduled_at.desc().nullslast())
                .limit(10)
            )
        ).all()
        now = datetime.now(UTC)
        return [
            {
                "appointmentId": str(r[0]),
                "startsAt": r[1].isoformat() if r[1] else None,
                "status": r[2].value,
                "doctorName": (r[3] or "").strip() or None,
                "upcoming": bool(r[1] and r[1] > now),
                "fact": f"appointment:{r[0]}",
            }
            for r in rows
        ]


def _same_person(said: str, on_card: str) -> bool:
    """Имя из сообщения и имя в карточке — про одного человека?

    Сравнение мягкое: «Айгуль» и «Айгүл» пишут по-разному, и разрывать из-за
    буквы разговор незачем. Но заметно разные имена — сигнал, что телефон
    чужой, и тогда решает человек.
    """
    a, b = said.strip().lower(), (on_card or "").strip().lower()
    if not a or not b:
        return True
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    stem = min(len(a), len(b), 4)
    return stem >= 3 and a[:stem] == b[:stem]
