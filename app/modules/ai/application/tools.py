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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.push.domain.events import format_som
from app.modules.schedule.application.services.availability_service import (
    build_availability_service,
)
from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
from app.modules.schedule.infrastructure.repositories.schedule_repository import (
    ScheduleRepository,
)
from app.shared.ai import AiToolDecl
from app.shared.db.enums import AppointmentStatus, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.membership import Membership
from app.shared.db.models.service import Service
from app.shared.db.models.specialization_translation import SpecializationTranslation

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
        description="Позвать человека: вопрос вне твоих полномочий или правил.",
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
    """Что нужно инструментам: клиника и пациент диалога."""

    clinic_id: uuid.UUID
    patient_id: uuid.UUID | None


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
        if name == "escalate":
            # Само действие делает reply-сервис; инструмент лишь подтверждает.
            return {"ok": True}
        return {"error": f"незнакомый инструмент {name}"}

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
        slots = [
            {
                "slot": s.span.start.isoformat(),
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
