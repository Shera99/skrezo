"""Напоминания о долге и повторном осмотре (Ф7).

Долг: завершённый приём с остатком по кассе старше N дней — один шаблон на
приём, без кнопок (о деньгах бот не разговаривает: ответ пациента уйдёт в
обычный диалог, а при необходимости — администратору). Повторный осмотр:
последний завершённый визит старше N месяцев и нет будущей записи — шаблон
с кнопками «Записаться» / «Не сейчас»; «Записаться» возвращает в диалог с
ботом, который умеет записывать. Всё по умениям матрицы и раз в сутки утром.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.modules.ai.application.outbound import OutboundTemplates
from app.modules.ai.domain.skills import caps_from, effective_level
from app.modules.ai.domain.texts import booking_text
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository
from app.modules.push.domain.events import format_som
from app.shared.db.enums import AppointmentStatus, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_reminder import PatientReminder
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

logger = structlog.get_logger(__name__)

RECALL_PREFIX = "recall:"
KIND_DEBT = "debt"
KIND_RECALL = "recall"
#: Вид напоминания → умение матрицы, которым оно включается.
_SKILL_OF = {KIND_DEBT: "debts", KIND_RECALL: "recall"}
_UPCOMING = (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED)


class PatientReminderService:
    def __init__(self, session: AsyncSession, wa_client: WhatsAppCloudClient) -> None:
        self._session = session
        self._outbound = OutboundTemplates(session, wa_client)

    # ── Рассылка (cron, раз в сутки) ────────────────────────────────────────

    async def send_debts(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        due_before = now - timedelta(days=settings.debt_reminder_days_after)
        sent = 0
        for clinic, binding in await self._clinics_with_skill(_SKILL_OF[KIND_DEBT]):
            debts = await CashRepository(self._session).list_debts(
                clinic.id, patient_id=None, own_membership_id=None
            )
            for debt in debts:
                if debt.scheduled_at > due_before:
                    continue
                if await self._already(KIND_DEBT, debt.appointment_id):
                    continue
                patient = await self._session.get(Patient, debt.patient_id)
                if patient is None:
                    continue
                ok = await self._send(
                    clinic,
                    binding,
                    patient,
                    kind=KIND_DEBT,
                    appointment_id=debt.appointment_id,
                    template_name=settings.wa_debt_reminder_template,
                    values=[patient.first_name, format_som(debt.outstanding_tiyin), clinic.name],
                    buttons=None,
                    now=now,
                )
                sent += int(ok)
        return sent

    async def send_recalls(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=30 * settings.recall_months)
        sent = 0
        for clinic, binding in await self._clinics_with_skill(_SKILL_OF[KIND_RECALL]):
            # Будущие записи — алиасом: без него exists() коррелирует с внешним
            # Appointment и теряет FROM.
            future = aliased(Appointment)
            upcoming = exists().where(
                and_(
                    future.patient_id == Appointment.patient_id,
                    future.clinic_id == clinic.id,
                    future.status.in_(_UPCOMING),
                    future.scheduled_at > now,
                )
            )
            # Последний завершённый визит каждого пациента — DISTINCT ON.
            latest = (
                select(Appointment)
                .join(Patient, Patient.id == Appointment.patient_id)
                .where(
                    Appointment.clinic_id == clinic.id,
                    Appointment.status == AppointmentStatus.COMPLETED,
                    ~upcoming,
                )
                .distinct(Appointment.patient_id)
                .order_by(Appointment.patient_id, Appointment.scheduled_at.desc())
                .limit(2000)
            )
            for appointment in (await self._session.execute(latest)).scalars():
                if appointment.scheduled_at is None or appointment.scheduled_at > cutoff:
                    continue
                if await self._already(KIND_RECALL, appointment.id):
                    continue
                patient = await self._session.get(Patient, appointment.patient_id)
                if patient is None:
                    continue
                buttons = (
                    [
                        f"{RECALL_PREFIX}{appointment.id}:book",
                        f"{RECALL_PREFIX}{appointment.id}:later",
                    ]
                    if settings.wa_recall_buttons
                    else None
                )
                ok = await self._send(
                    clinic,
                    binding,
                    patient,
                    kind=KIND_RECALL,
                    appointment_id=appointment.id,
                    template_name=settings.wa_recall_reminder_template,
                    values=[patient.first_name, str(settings.recall_months), clinic.name],
                    buttons=buttons,
                    now=now,
                )
                sent += int(ok)
        return sent

    # ── Кнопки повторного осмотра (инбаунд, мимо модели) ────────────────────

    async def handle_button(self, conversation: AiConversation, payload: str) -> str:
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        try:
            _, raw_id, action = payload.split(":", 2)
            appointment_id = uuid.UUID(raw_id)
        except ValueError:
            return booking_text("recall_gone", lang)
        reminder = (
            await self._session.execute(
                select(PatientReminder).where(
                    PatientReminder.kind == KIND_RECALL,
                    PatientReminder.appointment_id == appointment_id,
                    PatientReminder.clinic_id == conversation.clinic_id,
                )
            )
        ).scalar_one_or_none()
        patient = (
            await self._session.get(Patient, reminder.patient_id) if reminder is not None else None
        )
        own = reminder is not None and (
            reminder.patient_id == conversation.patient_id
            or (patient is not None and patient.phone == conversation.wa_phone)
        )
        if reminder is None or not own:
            return booking_text("recall_gone", lang)
        reminder.answered_at = datetime.now(UTC)
        reminder.status = "answered" if action == "book" else "snoozed"
        await self._session.commit()
        return booking_text("recall_book" if action == "book" else "recall_later", lang)

    # ── Вспомогательное ─────────────────────────────────────────────────────

    async def _clinics_with_skill(self, skill: str) -> list[tuple[Clinic, ClinicWhatsApp]]:
        rows = (
            await self._session.execute(
                select(Clinic, ClinicWhatsApp)
                .join(ClinicWhatsApp, ClinicWhatsApp.clinic_id == Clinic.id)
                .where(ClinicWhatsApp.active.is_(True))
            )
        ).all()
        return [
            (clinic, binding)
            for clinic, binding in rows
            if effective_level(clinic.ai_mode, caps_from(clinic.ai_skills)[skill])
            in ("assisted", "auto")
        ]

    async def _already(self, kind: str, appointment_id: uuid.UUID) -> bool:
        found = (
            await self._session.execute(
                select(PatientReminder.id).where(
                    PatientReminder.kind == kind, PatientReminder.appointment_id == appointment_id
                )
            )
        ).first()
        return found is not None

    async def _send(
        self,
        clinic: Clinic,
        binding: ClinicWhatsApp,
        patient: Patient,
        *,
        kind: str,
        appointment_id: uuid.UUID,
        template_name: str,
        values: list[str],
        buttons: list[str] | None,
        now: datetime,
    ) -> bool:
        conversation = await self._outbound.conversation_for(clinic.id, patient)
        reminder = PatientReminder(
            clinic_id=clinic.id,
            patient_id=patient.id,
            kind=kind,
            appointment_id=appointment_id,
            conversation_id=conversation.id,
            sent_at=now,
        )
        template = await self._outbound.template(clinic.id, template_name, patient, conversation)
        if template is None:
            reminder.status = "failed"
            reminder.note = f"нет одобренного шаблона {template_name}"
            self._session.add(reminder)
            await self._session.commit()
            logger.warning("patient_reminder_no_template", clinic_id=str(clinic.id), kind=kind)
            return False
        try:
            delivered = await self._outbound.send(
                clinic, binding, patient, conversation, template, values, buttons, now
            )
        except WhatsAppSendError as exc:
            reminder.status = "failed"
            reminder.note = str(exc)[:300]
            self._session.add(reminder)
            await self._session.commit()
            logger.warning("patient_reminder_send_failed", kind=kind, error=str(exc))
            return False
        reminder.wa_message_id = delivered.wa_message_id
        self._session.add(reminder)
        await self._session.commit()
        return True


def reminder_summary(debts: int, recalls: int) -> dict[str, Any]:
    return {"debts": debts, "recalls": recalls}
