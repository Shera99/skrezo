"""Инструменты ассистента (§6.1) — только чтение и предложения.

Красная зона: цены, слоты, врачи приходят ТОЛЬКО отсюда, из живой БД. Каждый
вызов журналируется в ``ai_tool_call`` — по нему валидатор сверит факты, а при
споре видно, откуда взялось каждое число. Инструментов записи здесь нет:
``propose_*``/``confirm_action`` — следующий инкремент.
"""

from __future__ import annotations

import re
import time as time_mod
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.application.booking import (
    CHANGEABLE_STATUSES,
    HOLD_BOOK,
    HOLD_CANCEL,
    HOLD_RESCHEDULE,
    BookingService,
    ProposeResult,
    earliest_bookable,
)
from app.modules.ai.domain.skills import DEFAULT_SKILLS, disabled_tools
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
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.ai import AiToolDecl
from app.shared.db.enums import AiMessageRole, AppointmentStatus, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.service import Service
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.realtime import publish_realtime
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
            "не обещай, что уже записал. Если у пациента уже есть запись в тот же "
            "или соседний день, инструмент вернёт existingAppointments вместо кнопки."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doctorId": {"type": "string"},
                "startsAt": {"type": "string", "description": "ISO из get_available_slots"},
                "serviceId": {"type": "string"},
                "note": {
                    "type": "string",
                    "description": (
                        "Жалоба, номер зуба или вторая процедура словами пациента — "
                        "врач увидит в записи"
                    ),
                },
                "secondBooking": {
                    "type": "boolean",
                    "description": (
                        "true — пациент СВОИМ сообщением подтвердил, что нужна ЕЩЁ одна "
                        "запись при уже существующей (existingAppointments)"
                    ),
                },
            },
            "required": ["doctorId", "startsAt"],
        },
    ),
    AiToolDecl(
        name="propose_reschedule",
        description=(
            "Перенести СВОЮ будущую запись пациента: appointmentId из "
            "get_patient_appointments (canChange=true), startsAt — ровно из "
            "get_available_slots; doctorId — только если пациент хочет другого врача. "
            "Пациенту уйдёт кнопка; перенос происходит ТОЛЬКО после кнопки — "
            "не говори «перенёс»."
        ),
        parameters={
            "type": "object",
            "properties": {
                "appointmentId": {"type": "string"},
                "startsAt": {"type": "string", "description": "ISO из get_available_slots"},
                "doctorId": {"type": "string"},
            },
            "required": ["appointmentId", "startsAt"],
        },
    ),
    AiToolDecl(
        name="propose_cancel",
        description=(
            "Отменить СВОЮ будущую запись пациента: appointmentId из "
            "get_patient_appointments (canChange=true). Пациенту уйдёт кнопка; "
            "отмена происходит ТОЛЬКО после кнопки — не говори «отменил»."
        ),
        parameters={
            "type": "object",
            "properties": {"appointmentId": {"type": "string"}},
            "required": ["appointmentId"],
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
    #: Действующие уровни умений (Ф7): off | shadow | assisted | auto.
    skills: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SKILLS))
    #: В этом ходу модель уже получила existingAppointments: ``secondBooking``
    #: до нового сообщения пациента не принимается (см. ``_existing_visits``).
    existing_shown: bool = False

    @property
    def clinic_id(self) -> uuid.UUID:
        return self.conversation.clinic_id

    @property
    def patient_id(self) -> uuid.UUID | None:
        return self.conversation.patient_id


#: Тексты кнопки по виду предложения (Ф7): (тело, подпись кнопки).
_BUTTON_TEXTS: dict[str, tuple[str, str]] = {
    HOLD_BOOK: ("confirm_button", "confirm_title"),
    HOLD_RESCHEDULE: ("reschedule_button", "reschedule_title"),
    HOLD_CANCEL: ("cancel_button", "cancel_title"),
}

#: Число в пожелании пациента: номер зуба, количество.
_NOTE_NUMBER = re.compile(r"\d+")

#: Инструменты, которые отправляют пациенту кнопку подтверждения, и вид кнопки.
BUTTON_TOOLS: dict[str, str] = {
    "propose_appointment": HOLD_BOOK,
    "propose_reschedule": HOLD_RESCHEDULE,
    "propose_cancel": HOLD_CANCEL,
}


def button_title(tool: str, lang: str) -> str:
    """Подпись кнопки, которую отправил инструмент: «Подтвердить», «Перенести»…"""
    _, title_key = _BUTTON_TEXTS[BUTTON_TOOLS.get(tool, HOLD_BOOK)]
    return booking_text(title_key, lang)


#: Что можно звать в сухом прогоне регрессии — ничего не меняет в базе.
_READ_ONLY_TOOLS = frozenset(
    {"get_services", "get_doctors", "get_available_days", "get_available_slots"}
)


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

    async def dry_dispatch(
        self, context: ToolContext, name: str, args: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        """Инструменты чтения без журнала и без диалога — для сухого прогона (Ф6).

        Всё, что пишет (пациент, резерв слота, глоссарий), в прогоне недоступно:
        модель получает отказ данными, как при любой ошибке инструмента.
        """
        if name not in _READ_ONLY_TOOLS:
            return {"error": f"инструмент {name} недоступен в прогоне регрессии"}
        try:
            return await self._dispatch(context, name, args)
        except Exception as exc:  # как в боевом execute: отказ уходит модели текстом
            return {"error": str(exc)[:300]}

    async def _dispatch(
        self, context: ToolContext, name: str, args: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        if name in disabled_tools(context.skills):
            # Вторая линия: декларации уже отфильтрованы, но модель могла
            # вспомнить инструмент по истории диалога.
            return {"error": f"инструмент {name} отключён настройками клиники"}
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
        if name == "propose_reschedule":
            return await self._propose_change(context, args, kind=HOLD_RESCHEDULE)
        if name == "propose_cancel":
            return await self._propose_change(context, args, kind=HOLD_CANCEL)
        if name == "escalate":
            # Само действие делает reply-сервис; инструмент лишь подтверждает.
            return {"ok": True}
        return {"error": f"незнакомый инструмент {name}"}

    async def _get_days(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        booking = BookingService(self._session)
        now = datetime.now(UTC)
        date_from = (
            datetime.fromisoformat(str(args["dateFrom"])).replace(tzinfo=UTC)
            if args.get("dateFrom")
            else now
        )
        # Прошедшая дата (модель ошиблась годом) — ищем от сегодня, а не в прошлом.
        date_from = max(date_from, now)
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

    def _past_time_error(self, context: ToolContext, starts_at: datetime) -> dict[str, Any] | None:
        """Ошибка для модели, если время записи уже прошло (иначе ``None``).

        Кнопка на прошедшее время всё равно не сработала бы при подтверждении,
        но пациент успел бы получить приглашение «прийти» вчера.
        """
        now = datetime.now(UTC)
        if starts_at > now:
            return None
        local_now = now.astimezone(self._clinic_tz(context))
        return {
            "error": (
                "это время уже прошло — возьми слот из get_available_slots на сегодня или позже"
            ),
            "now": local_now.isoformat(timespec="minutes"),
        }

    async def _propose(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        booking = BookingService(self._session)
        starts_at = datetime.fromisoformat(str(args["startsAt"]))
        if starts_at.tzinfo is None:
            return {"error": "startsAt без таймзоны — возьми значение из get_available_slots"}
        past = self._past_time_error(context, starts_at)
        if past is not None:
            return past
        existing = await self._existing_visits(context, booking, args, starts_at)
        if existing is not None:
            return existing
        invented = await self._invented_note_numbers(context, args.get("note"))
        if invented:
            return {
                "error": (
                    f"В note числа {', '.join(invented)}, которых пациент не писал. "
                    "Перепиши note словами пациента — номер зуба врач увидит в записи."
                )
            }
        result = await booking.propose(
            context.conversation,
            doctor_membership_id=uuid.UUID(str(args["doctorId"])),
            starts_at=starts_at,
            service_id=uuid.UUID(str(args["serviceId"])) if args.get("serviceId") else None,
            note=str(args["note"]) if args.get("note") else None,
        )
        try:
            button_sent = await self._send_button(context, HOLD_BOOK, result, starts_at)
        except WhatsAppSendError as exc:
            return {"error": f"кнопка не отправилась: {exc}"}
        card = {
            **result.card,
            "startsAt": starts_at.astimezone(self._clinic_tz(context)).isoformat(),
        }
        return {**card, "buttonSent": button_sent, **self._handoff(context, "booking", card)}

    async def _invented_note_numbers(self, context: ToolContext, note: Any) -> list[str]:
        """Числа из ``note``, которых нет в сообщениях пациента этого диалога.

        Пожелание уходит врачу с подписью «со слов пациента»: «8 зуб»,
        пересказанный как «18 зуб», — неверный зуб в записи. Сверяются только
        числа (номер зуба, количество): слова модель вправе пересказать.
        """
        numbers = set(_NOTE_NUMBER.findall(str(note or "")))
        if not numbers:
            return []
        said = (
            await self._session.execute(
                select(func.coalesce(AiMessage.transcript, AiMessage.body)).where(
                    AiMessage.conversation_id == context.conversation.id,
                    AiMessage.role == AiMessageRole.PATIENT,
                )
            )
        ).scalars()
        known = {n for text in said for n in _NOTE_NUMBER.findall(text or "")}
        return sorted(numbers - known)

    async def _existing_visits(
        self,
        context: ToolContext,
        booking: BookingService,
        args: dict[str, Any],
        starts_at: datetime,
    ) -> dict[str, Any] | None:
        """Запись пациента рядом с новой — вместо второй кнопки, пока он не сказал «да».

        Инцидент 13.10.2026: пациент записан на 13:00, пишет «хотел записаться»,
        и бот молча отправил вторую кнопку — на 13:30. Модель могла не знать о
        записи (подтверждение кнопкой она не видела), поэтому проверка здесь, а
        не в промпте.

        Мешает только запись в тот же или соседний день: плановый визит через
        месяц не повод спрашивать человека с болью «оставить или перенести» —
        «перенести» он поймёт как «принять раньше» и потеряет плановый визит.

        ``secondBooking=true`` — пациент подтвердил, что нужна ещё одна запись.
        В том же ходу, где модель только что узнала о записи, флаг не
        принимается: согласие пациента — это его новое сообщение, а не
        «исправление» модели повторным вызовом.
        """
        patient_id = context.conversation.patient_id
        if patient_id is None:
            return None
        if args.get("secondBooking") is True:
            if context.existing_shown:
                return {
                    "error": (
                        "secondBooking — только после ответа пациента. Сначала назови "
                        "ему существующую запись и спроси, нужна ли ещё одна."
                    )
                }
            return None
        tz = self._clinic_tz(context)
        day = starts_at.astimezone(tz).date()
        visits = [
            visit
            for visit in await booking.upcoming_visits(context.clinic_id, patient_id)
            if abs((visit.starts_at.date() - day).days) <= 1
        ]
        if not visits:
            return None
        context.existing_shown = True
        return {
            "error": (
                "У пациента уже есть запись рядом с этим временем — вторую кнопку не "
                "отправляй молча. Назови пациенту его запись и спроси: оставить её, "
                "перенести (propose_reschedule) или нужна ещё одна. Ещё одна — только "
                "после ответа пациента: тогда propose_appointment с secondBooking=true."
            ),
            "existingAppointments": [
                {
                    "appointmentId": str(visit.appointment_id),
                    "startsAt": visit.starts_at.isoformat(),
                    "date": visit.starts_at.strftime("%d.%m"),
                    "time": visit.starts_at.strftime("%H:%M"),
                    "doctorName": visit.doctor_name,
                    "service": visit.service_name,
                    "fact": f"appointment:{visit.appointment_id}",
                }
                for visit in visits
            ],
        }

    async def _propose_change(
        self, context: ToolContext, args: dict[str, Any], *, kind: str
    ) -> dict[str, Any]:
        """Перенос или отмена своей записи (Ф7): резерв/пометка + кнопка."""
        booking = BookingService(self._session)
        appointment_id = uuid.UUID(str(args["appointmentId"]))
        if kind == HOLD_RESCHEDULE:
            starts_at = datetime.fromisoformat(str(args["startsAt"]))
            if starts_at.tzinfo is None:
                return {"error": "startsAt без таймзоны — возьми значение из get_available_slots"}
            past = self._past_time_error(context, starts_at)
            if past is not None:
                return past
            result = await booking.propose_reschedule(
                context.conversation,
                appointment_id=appointment_id,
                starts_at=starts_at,
                doctor_membership_id=(
                    uuid.UUID(str(args["doctorId"])) if args.get("doctorId") else None
                ),
            )
            when = starts_at
        else:
            result = await booking.propose_cancel(
                context.conversation, appointment_id=appointment_id
            )
            when = datetime.fromisoformat(str(result.card["startsAt"]))
        try:
            button_sent = await self._send_button(context, kind, result, when)
        except WhatsAppSendError as exc:
            return {"error": f"кнопка не отправилась: {exc}"}
        tz = self._clinic_tz(context)
        card = {**result.card, "startsAt": when.astimezone(tz).isoformat()}
        if card.get("fromStartsAt"):
            # Прежнее время переносимой записи — тоже по часам клиники, не UTC.
            card["fromStartsAt"] = (
                datetime.fromisoformat(str(card["fromStartsAt"])).astimezone(tz).isoformat()
            )
        return {**card, "buttonSent": button_sent, **self._handoff(context, "changes", card)}

    @staticmethod
    def _handoff(context: ToolContext, skill: str, card: dict[str, Any]) -> dict[str, Any]:
        """Потолок умения assisted (Ф7): действие за администратором — эскалация с карточкой."""
        if context.skills.get(skill, "auto") != "assisted":
            return {}
        reason = "booking_needs_human" if skill == "booking" else "change_needs_human"
        when = datetime.fromisoformat(str(card["startsAt"])).strftime("%d.%m %H:%M")
        bits = [str(card.get("service") or "Приём"), f"врач {card.get('doctor')}", when]
        if skill == "changes":
            bits.insert(0, "отмена" if card.get("kind") == HOLD_CANCEL else "перенос")
        return {"_escalate": reason, "_note": ", ".join(bits)}

    async def _send_button(
        self, context: ToolContext, kind: str, result: ProposeResult, when: datetime
    ) -> bool:
        """Кнопка подтверждения — только при действующем уровне умения auto.

        В shadow/assisted пациент бота не видит; при потолке умения assisted
        (Ф7) кнопку заменяет эскалация — запись подтверждает администратор.

        Сообщение с кнопкой пишется в переписку (инцидент 13.10.2026): без него
        администратор видел в чате «Подтвердить» пациента из ниоткуда, а модель
        на следующем ходу не знала, что запись уже предложена, — и предлагала её
        заново. Галочки доставки тоже находят эту строку по ``wa_message_id``.
        """
        skill = "booking" if kind == HOLD_BOOK else "changes"
        if context.skills.get(skill, "auto") != "auto" or context.binding_phone_number_id is None:
            return False
        lang = "ky" if context.conversation.lang is Lang.KG else "ru"
        text_key, title_key = _BUTTON_TEXTS[kind]
        body = booking_text(text_key, lang).format(
            summary=result.card.get("service") or "Приём",
            when=when.astimezone(self._clinic_tz(context)).strftime("%d.%m %H:%M"),
        )
        sent = await context.wa_client.send_confirm_button(
            context.binding_phone_number_id,
            context.conversation.wa_phone,
            body,
            result.confirmation_id,
            booking_text(title_key, lang),
        )
        now = datetime.now(UTC)
        await WhatsAppRepository(self._session).add_message(
            context.conversation,
            role=AiMessageRole.SYSTEM,
            body=body,
            wa_message_id=sent.wa_message_id,
            sent_at=now,
        )
        context.conversation.last_msg_at = now
        await publish_realtime(
            self._session,
            context.clinic_id,
            "conversation.message",
            {"conversationId": str(context.conversation.id), "direction": "out"},
        )
        return True

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
        tz = self._clinic_tz(context)
        now = datetime.now(UTC)
        today = now.astimezone(tz).date()
        if day < today:
            # Модель без строки «СЕЙЧАС» брала год из памяти (2023, 2024), и
            # расписание по прошедшей дате выглядело свободным. Ошибка с сегодняшней
            # датой даёт ей исправиться в том же ходе.
            return {
                "error": "эта дата уже прошла — слоты только на сегодня и позже",
                "today": today.isoformat(),
            }
        schedule = ScheduleRepository(self._session)
        use_case = GetSlotsUseCase(schedule, build_availability_service(self._session))
        if doctor_id is None and service_id is None:
            # Правило use-case'а: без врача и услуги подбирать не по чему.
            doctors = await self._get_doctors(context)
            return {"error": "нужен doctorId или serviceId", "doctors": doctors}
        plan = await use_case.execute(context.clinic_id, doctor_id, day, service_id)
        # Наружу — местное время клиники: модель называет слот пациенту дословно,
        # и UTC в её выдаче означал бы приглашение прийти на несколько часов раньше.
        # Прошедшее и слишком близкое сегодня не предлагаем: в 14:35 слоты на 09:00
        # и на 14:45 пациенту уже недоступны (запас — AI_SLOT_LEAD_MINUTES).
        not_before = earliest_bookable(now)
        upcoming = [s for s in plan.slots if s.span.start >= not_before]
        slots = [
            {
                "slot": s.span.start.astimezone(tz).isoformat(),
                # Готовое «09:30» — его модель и называет пациенту, и валидатор
                # узнаёт как факт без разбора ISO-строки.
                "time": s.span.start.astimezone(tz).strftime("%H:%M"),
                "doctorId": str(s.doctor_membership_id),
                # Метка факта — тоже по часам клиники: UTC-время («03:30» вместо
                # «09:30») в контексте хода валидатор счёл бы законным фактом, и
                # сдвиг на 6 часов проходил бы мимо V02.
                "fact": f"slot:{s.doctor_membership_id}:{s.span.start.astimezone(tz).isoformat()}",
            }
            for s in upcoming[:10]
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
        tz = self._clinic_tz(context)
        return [
            {
                "appointmentId": str(r[0]),
                # Время клиники, а не UTC из базы: «Когда моя запись?» модель
                # отвечает дословно, и UTC означал бы неверное время приёма.
                "startsAt": r[1].astimezone(tz).isoformat() if r[1] else None,
                "time": r[1].astimezone(tz).strftime("%H:%M") if r[1] else None,
                "status": r[2].value,
                "doctorName": (r[3] or "").strip() or None,
                "upcoming": bool(r[1] and r[1] > now),
                # Ф7: можно ли перенести/отменить через propose_reschedule/propose_cancel.
                "canChange": bool(r[1] and r[1] > now and r[2] in CHANGEABLE_STATUSES),
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


def tool_decls_for(skills: Mapping[str, str]) -> list[AiToolDecl]:
    """Декларации инструментов без выключенных умений (Ф7)."""
    hidden = disabled_tools(skills)
    return [decl for decl in TOOL_DECLS if decl.name not in hidden]
