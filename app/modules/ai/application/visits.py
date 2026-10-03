"""Подтверждение визита из напоминания (Ф7).

За сутки до приёма пациенту уходит шаблон WhatsApp с кнопками «Приду» и
«Перенести» (шаблон — потому что окно 24 часов давно закрыто). «Приду»
переводит приём в ``confirmed`` той же механикой кнопки, что и запись:
детерминированно, мимо модели, штатным use-case правки приёма. «Перенести»
возвращает пациента в диалог с ботом — перенос он умеет (Ф7·2). Без ответа
к контрольному сроку — событие администратору: не подтверждённый визит
важнее подтверждённого.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.modules.ai.domain.skills import effective_level
from app.modules.ai.domain.texts import booking_text
from app.modules.appointments.application.use_cases.update_appointment import (
    UpdateAppointmentUseCase,
)
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.infrastructure.repositories.audit_repository import AuditRepository
from app.modules.push.application.outbox import emit_event
from app.modules.schedule.application.services.availability_service import (
    build_availability_service,
)
from app.modules.whatsapp.application.services.credentials import client_for
from app.modules.whatsapp.application.use_cases.send_template import render_template_body
from app.modules.whatsapp.domain.lifecycle import reopen_if_closed
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiMessageRole, AppointmentStatus, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_reminder import AppointmentReminder
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.patient import Patient
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

logger = structlog.get_logger(__name__)

#: Префикс payload кнопок напоминания: ``visit:<appointment_id>:confirm|change``.
VISIT_PREFIX = "visit:"


def _lang_of(patient: Patient | None, conversation: AiConversation | None) -> str:
    if conversation is not None and conversation.lang is Lang.KG:
        return "ky"
    if patient is not None and patient.preferred_lang is Lang.KG:
        return "ky"
    return "ru"


class VisitReminderService:
    def __init__(self, session: AsyncSession, wa_client: WhatsAppCloudClient) -> None:
        self._session = session
        self._wa = wa_client
        self._repository = WhatsAppRepository(session)

    # ── Рассылка (cron) ──────────────────────────────────────────────────────

    async def send_due(self, now: datetime | None = None) -> int:
        """Напомнить о приёмах, вошедших в горизонт; вернуть число отправленных.

        Приём попадает в рассылку один раз (уникальность по приёму): между
        контрольным сроком и горизонтом, статус ``scheduled``, у клиники живой
        WhatsApp, включённое умение «напоминания» и одобренный шаблон.
        """
        now = now or datetime.now(UTC)
        horizon = now + timedelta(hours=settings.visit_reminder_hours_before)
        cutoff = now + timedelta(hours=settings.visit_unconfirmed_hours_before)
        rows = (
            await self._session.execute(
                select(Appointment, Patient, Clinic, ClinicWhatsApp)
                .join(Patient, Patient.id == Appointment.patient_id)
                .join(Clinic, Clinic.id == Appointment.clinic_id)
                .join(ClinicWhatsApp, ClinicWhatsApp.clinic_id == Clinic.id)
                .outerjoin(
                    AppointmentReminder, AppointmentReminder.appointment_id == Appointment.id
                )
                .where(
                    Appointment.status == AppointmentStatus.SCHEDULED,
                    Appointment.scheduled_at > cutoff,
                    Appointment.scheduled_at <= horizon,
                    ClinicWhatsApp.active.is_(True),
                    AppointmentReminder.id.is_(None),
                )
                .order_by(Appointment.scheduled_at)
                .limit(500)
            )
        ).all()
        sent = 0
        for appointment, patient, clinic, binding in rows:
            if effective_level(clinic.ai_mode, self._cap(clinic)) not in ("assisted", "auto"):
                continue
            if await self._send_one(appointment, patient, clinic, binding, now):
                sent += 1
        return sent

    async def _send_one(
        self,
        appointment: Appointment,
        patient: Patient,
        clinic: Clinic,
        binding: ClinicWhatsApp,
        now: datetime,
    ) -> bool:
        conversation = await self._repository.conversation_by_phone(clinic.id, patient.phone)
        if conversation is None:
            conversation = await self._repository.create_conversation(
                clinic.id, patient.phone, patient.id
            )
        else:
            # Диалог постоянный: напоминание в закрытый — возобновляет его.
            reopen_if_closed(conversation)
        lang = _lang_of(patient, conversation)
        template = await self._template(clinic.id, lang)
        reminder = AppointmentReminder(
            clinic_id=clinic.id,
            appointment_id=appointment.id,
            conversation_id=conversation.id,
            sent_at=now,
        )
        if template is None:
            reminder.status = "failed"
            reminder.note = f"нет одобренного шаблона {settings.wa_visit_reminder_template}"
            self._session.add(reminder)
            await self._session.commit()
            logger.warning("visit_reminder_no_template", clinic_id=str(clinic.id))
            return False
        assert appointment.scheduled_at is not None
        local = appointment.scheduled_at.astimezone(ZoneInfo(clinic.timezone or "Asia/Bishkek"))
        values = [patient.first_name, local.strftime("%d.%m"), local.strftime("%H:%M"), clinic.name]
        variables = values[: len(template.variables)]
        buttons = (
            [f"{VISIT_PREFIX}{appointment.id}:confirm", f"{VISIT_PREFIX}{appointment.id}:change"]
            if settings.wa_visit_reminder_buttons
            else None
        )
        try:
            result = await client_for(self._wa, binding).send_template(
                binding.phone_number_id,
                patient.phone,
                template.meta_name,
                template.lang,
                variables,
                buttons=buttons,
            )
        except WhatsAppSendError as exc:
            reminder.status = "failed"
            reminder.note = str(exc)[:300]
            self._session.add(reminder)
            await self._session.commit()
            logger.warning("visit_reminder_send_failed", appointment_id=str(appointment.id))
            return False
        reminder.wa_message_id = result.wa_message_id
        self._session.add(reminder)
        await self._repository.add_message(
            conversation,
            role=AiMessageRole.SYSTEM,
            body=render_template_body(template.body_preview, variables),
            wa_message_id=result.wa_message_id,
            sent_at=now,
        )
        conversation.last_msg_at = now
        await publish_realtime(
            self._session,
            clinic.id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": "out"},
        )
        await self._session.commit()
        return True

    async def escalate_unanswered(self, now: datetime | None = None) -> int:
        """Напоминание без ответа к контрольному сроку — событие администратору."""
        now = now or datetime.now(UTC)
        cutoff = now + timedelta(hours=settings.visit_unconfirmed_hours_before)
        rows = (
            await self._session.execute(
                select(AppointmentReminder, Appointment)
                .join(Appointment, Appointment.id == AppointmentReminder.appointment_id)
                .where(
                    AppointmentReminder.status == "sent",
                    Appointment.scheduled_at <= cutoff,
                )
                .limit(500)
            )
        ).all()
        escalated = 0
        for reminder, appointment in rows:
            reminder.answered_at = now
            if appointment.status is not AppointmentStatus.SCHEDULED:
                # Подтвердили из кабинета, отменили или перенесли — вопрос снят.
                reminder.status = (
                    "confirmed"
                    if appointment.status is AppointmentStatus.CONFIRMED
                    else "no_answer"
                )
                reminder.note = f"приём в статусе {appointment.status.value}"
                continue
            reminder.status = "no_answer"
            await emit_event(
                self._session,
                clinic_id=appointment.clinic_id,
                type_="appointment.unconfirmed",
                entity_type="appointment",
                entity_id=appointment.id,
            )
            escalated += 1
        await self._session.commit()
        return escalated

    # ── Кнопка (инбаунд, мимо модели) ────────────────────────────────────────

    async def handle_button(self, conversation: AiConversation, payload: str) -> str:
        """«Приду» → confirmed, «Перенести» → в диалог; вернуть текст пациенту."""
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        try:
            _, raw_id, action = payload.split(":", 2)
            appointment_id = uuid.UUID(raw_id)
        except ValueError:
            return booking_text("visit_gone", lang)
        appointment = await self._session.get(Appointment, appointment_id)
        if appointment is None or appointment.clinic_id != conversation.clinic_id:
            return booking_text("visit_gone", lang)
        patient = await self._session.get(Patient, appointment.patient_id)
        own = appointment.patient_id == conversation.patient_id or (
            patient is not None and patient.phone == conversation.wa_phone
        )
        if not own or appointment.scheduled_at is None:
            return booking_text("visit_gone", lang)
        reminder = (
            await self._session.execute(
                select(AppointmentReminder).where(
                    AppointmentReminder.appointment_id == appointment.id
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        clinic = await self._session.get(Clinic, appointment.clinic_id)
        local = appointment.scheduled_at.astimezone(
            ZoneInfo((clinic.timezone if clinic else None) or "Asia/Bishkek")
        )
        when = local.strftime("%d.%m %H:%M")

        if action == "change":
            if appointment.status not in (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED):
                return booking_text("visit_gone", lang)
            if reminder is not None:
                reminder.status = "change_requested"
                reminder.answered_at = now
            await self._session.commit()
            return booking_text("visit_change", lang)

        if appointment.status is AppointmentStatus.CONFIRMED:
            return booking_text("visit_already", lang).format(when=when)
        if appointment.status is not AppointmentStatus.SCHEDULED or appointment.scheduled_at <= now:
            return booking_text("visit_gone", lang)
        use_case = UpdateAppointmentUseCase(
            self._session,
            AppointmentRepository(self._session),
            build_availability_service(self._session),
            AuditService(self._session, AuditRepository(self._session)),
        )
        try:
            await use_case.execute(
                appointment.clinic_id,
                appointment.id,
                {"status": AppointmentStatus.CONFIRMED},
                actor_id=None,
                via="assistant",
            )
        except AppError as exc:
            await self._session.rollback()
            logger.info("visit_confirm_failed", appointment_id=str(appointment.id), error=exc.code)
            return booking_text("visit_gone", lang)
        if reminder is not None:
            reminder.status = "confirmed"
            reminder.answered_at = now
            await self._session.commit()
        return booking_text("visit_confirmed", lang).format(when=when)

    # ── Вспомогательное ─────────────────────────────────────────────────────

    @staticmethod
    def _cap(clinic: Clinic) -> str:
        value = (clinic.ai_skills or {}).get("reminders")
        return str(value) if value in ("off", "assisted", "auto") else "off"

    async def _template(self, clinic_id: uuid.UUID, lang: str) -> WhatsAppTemplate | None:
        """Одобренный шаблон напоминания — свой или общий, на языке пациента."""
        candidates = await self._repository.templates_by_name(
            clinic_id, settings.wa_visit_reminder_template
        )
        for template in candidates:
            if template.lang.lower().startswith(lang):
                return template
        return next((t for t in candidates if t.lang.lower().startswith("ru")), None) or (
            candidates[0] if candidates else None
        )
