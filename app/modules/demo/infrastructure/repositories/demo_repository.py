"""Запросы демо-клиники: состояние, удаление показанного, данные для нового дня.

⚠️ Пациент глобальный: его карточку видят все клиники, где он лечился. Поэтому
сброс удаляет карточку, только если её завела сама демо-клиника и больше на неё
никто не ссылается, — карточка человека из другой клиники не пострадает, даже
если его записали в демо.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    CursorResult,
    Delete,
    Select,
    and_,
    delete,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.demo.domain.models import DueClinic, Purged
from app.shared.db.enums import AiConversationStatus, AppointmentStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_summary import AiSummary
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_demo import ClinicDemo
from app.shared.db.models.event import Event
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_file import PatientFile
from app.shared.db.models.patient_reminder import PatientReminder
from app.shared.db.models.reminder_mark import ReminderMark
from app.shared.db.models.service import Service
from app.shared.db.models.slot_hold import SlotHold
from app.shared.db.models.whatsapp_history_message import WhatsAppHistoryMessage

#: Всё, что привязано к клинике и моменту создания и уходит вместе с перепиской.
_CHAT_MODELS: tuple[Any, ...] = (
    AiToolCall,
    AiEscalation,
    SlotHold,
    AiSummary,
    WhatsAppHistoryMessage,
    PatientReminder,
    AiMessage,
)

#: Где на пациента может ссылаться клиника. Осталась хоть одна строка — карточка
#: нужна кому-то ещё, и сброс её не трогает.
_PATIENT_LINKS: tuple[Any, ...] = (
    Appointment,
    CashPayment,
    AiConversation,
    AiSummary,
    PatientFile,
    PatientReminder,
    ReminderMark,
    SlotHold,
)

#: События, которые без удалённых записей повисли бы в ленте ссылкой в никуда.
_EVENT_ENTITIES = ("appointment", "conversation", "patient")


class DemoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Состояние ────────────────────────────────────────────────────────────

    async def clinic(self, clinic_id: uuid.UUID) -> Clinic | None:
        return await self._session.get(Clinic, clinic_id)

    async def state(self, clinic_id: uuid.UUID) -> ClinicDemo | None:
        return await self._session.get(ClinicDemo, clinic_id, populate_existing=True)

    async def add_state(self, state: ClinicDemo) -> None:
        self._session.add(state)
        await self._session.flush()

    async def db_now(self) -> datetime:
        """Часы БД — по ним же проставляется ``created_at``, с которым сравнивают."""
        return (await self._session.execute(select(func.now()))).scalar_one()

    async def lock(self) -> None:
        """Сбросы идут по одному: номера выдуманных пациентов общие на платформу."""
        key = func.hashtextextended("demo-reset", 0)
        await self._session.execute(select(func.pg_advisory_xact_lock(key)))

    async def demo_clinics(self) -> list[DueClinic]:
        rows = await self._session.execute(
            select(Clinic.id, Clinic.timezone, ClinicDemo.reset_at)
            .join(ClinicDemo, ClinicDemo.clinic_id == Clinic.id)
            .where(Clinic.demo.is_(True))
            .order_by(Clinic.created_at)
        )
        return [DueClinic(clinic_id=r[0], timezone=r[1], reset_at=r[2]) for r in rows.all()]

    # ── Удаление ─────────────────────────────────────────────────────────────

    async def purge(
        self,
        clinic_id: uuid.UUID,
        since: datetime,
        appointment_ids: Sequence[uuid.UUID],
        patient_ids: Sequence[uuid.UUID],
    ) -> Purged:
        """Убрать появившееся с ``since`` и собранное прошлым сбросом.

        Порядок важен: авторство карточек ассистента видно по диалогам, поэтому
        кандидаты собираются до их удаления, а сами карточки удаляются последними
        — когда ссылок демо-клиники уже нет и видно, остались ли чужие.
        """
        purged = Purged()
        candidates = await self._own_patients(clinic_id, since, patient_ids)

        purged.conversations = await self._purge_chats(clinic_id, since)
        await self._run(
            delete(ReminderMark).where(
                ReminderMark.clinic_id == clinic_id, ReminderMark.marked_at >= since
            )
        )
        purged.file_paths = await self._purge_files(clinic_id, since)

        ours = or_(Appointment.created_at >= since, Appointment.id.in_(appointment_ids))
        purged.payments = await self._run(
            delete(CashPayment).where(
                CashPayment.clinic_id == clinic_id,
                or_(
                    CashPayment.created_at >= since,
                    CashPayment.appointment_id.in_(appointment_ids),
                ),
            )
        )
        purged.appointments = await self._run(
            delete(Appointment).where(Appointment.clinic_id == clinic_id, ours)
        )
        await self._run(
            delete(Event).where(
                Event.clinic_id == clinic_id,
                Event.occurred_at >= since,
                Event.entity_type.in_(_EVENT_ENTITIES),
            )
        )
        if candidates:
            purged.patients = await self._run(
                delete(Patient).where(
                    Patient.id.in_(candidates),
                    *(~exists().where(link.patient_id == Patient.id) for link in _PATIENT_LINKS),
                )
            )
        return purged

    async def _own_patients(
        self, clinic_id: uuid.UUID, since: datetime, seeded: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Карточки, которые завела сама демо-клиника: сбросом, руками, ассистентом."""
        by_hand: Select[Any] = select(AuditLog.entity_id).where(
            AuditLog.clinic_id == clinic_id,
            AuditLog.action == "patient.create",
            AuditLog.entity_id.is_not(None),
            AuditLog.created_at >= since,
        )
        by_assistant = and_(
            Patient.source == "assistant",
            exists().where(
                AiConversation.clinic_id == clinic_id, AiConversation.patient_id == Patient.id
            ),
        )
        rows = await self._session.execute(
            select(Patient.id).where(
                or_(
                    Patient.id.in_(seeded),
                    and_(Patient.created_at >= since, or_(Patient.id.in_(by_hand), by_assistant)),
                )
            )
        )
        return list(rows.scalars())

    async def _purge_chats(self, clinic_id: uuid.UUID, since: datetime) -> int:
        """Переписка с ``since``: новые диалоги — целиком, старые — до прежнего вида."""
        for model in _CHAT_MODELS:
            await self._run(
                delete(model).where(model.clinic_id == clinic_id, model.created_at >= since)
            )
        removed = await self._run(
            delete(AiConversation).where(
                AiConversation.clinic_id == clinic_id, AiConversation.created_at >= since
            )
        )
        # Диалог на номер один и постоянный: начатый до включения демо остаётся,
        # но без показанного — закрытым и с датой последнего оставшегося сообщения.
        last = (
            select(func.max(func.coalesce(AiMessage.sent_at, AiMessage.created_at)))
            .where(AiMessage.conversation_id == AiConversation.id)
            .scalar_subquery()
        )
        await self._session.execute(
            update(AiConversation)
            .where(AiConversation.clinic_id == clinic_id, AiConversation.last_msg_at >= since)
            .values(
                last_msg_at=func.coalesce(last, AiConversation.created_at),
                status=AiConversationStatus.CLOSED,
            )
            .execution_options(synchronize_session=False)
        )
        return removed

    async def _purge_files(self, clinic_id: uuid.UUID, since: datetime) -> list[str]:
        """Файлы, загруженные клиникой на показах; вернуть пути для уборки с диска."""
        rows = await self._session.execute(
            delete(PatientFile)
            .where(PatientFile.clinic_id == clinic_id, PatientFile.created_at >= since)
            .returning(PatientFile.stored_path)
            .execution_options(synchronize_session=False)
        )
        return list(rows.scalars())

    async def _run(self, statement: Delete) -> int:
        result = await self._session.execute(statement.execution_options(synchronize_session=False))
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    # ── Данные для нового дня ────────────────────────────────────────────────

    async def priced_services(self, clinic_id: uuid.UUID) -> list[Service]:
        """Действующие услуги с ценой, дорогие сверху."""
        rows = await self._session.execute(
            select(Service)
            .where(
                Service.clinic_id == clinic_id,
                Service.active.is_(True),
                Service.price_tiyin > 0,
            )
            .order_by(Service.price_tiyin.desc(), Service.name)
        )
        return list(rows.scalars())

    async def taken_phones(self, phones: Sequence[str]) -> set[str]:
        rows = await self._session.execute(select(Patient.phone).where(Patient.phone.in_(phones)))
        return set(rows.scalars())

    async def is_doctor_busy(
        self, doctor_id: uuid.UUID, starts_at: datetime, ends_at: datetime
    ) -> bool:
        """Занят ли врач — тем же правилом, что держит констрейнт пересечений."""
        span = func.appointment_tstzrange(Appointment.scheduled_at, Appointment.duration_min)
        return bool(
            (
                await self._session.execute(
                    select(
                        exists().where(
                            Appointment.doctor_membership_id == doctor_id,
                            Appointment.status.not_in(
                                (AppointmentStatus.CANCELLED, AppointmentStatus.DRAFT)
                            ),
                            span.op("&&")(func.tstzrange(starts_at, ends_at)),
                        )
                    )
                )
            ).scalar_one()
        )

    async def add_all(self, rows: Sequence[Any]) -> None:
        self._session.add_all(rows)
        await self._session.flush()
