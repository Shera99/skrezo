"""Исходящие шаблоны пациентам по расписанию — общая механика (Ф7).

Шаблон (свой или общий платформы) на языке пациента, копия в истории
диалога, живая привязка WhatsApp. Используется напоминаниями о долге и
повторном осмотре; напоминание о визите живёт в ``visits.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp.application.use_cases.send_template import render_template_body
from app.modules.whatsapp.domain.lifecycle import reopen_if_closed
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiMessageRole, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.patient import Patient
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient


@dataclass(frozen=True, slots=True)
class Delivered:
    wa_message_id: str
    conversation: AiConversation


class OutboundTemplates:
    def __init__(self, session: AsyncSession, wa_client: WhatsAppCloudClient) -> None:
        self._session = session
        self._wa = wa_client
        self._repository = WhatsAppRepository(session)

    async def conversation_for(self, clinic_id: uuid.UUID, patient: Patient) -> AiConversation:
        """Диалог пациента в клинике — единственный; закрытый возобновляется."""
        conversation = await self._repository.conversation_by_phone(clinic_id, patient.phone)
        if conversation is None:
            return await self._repository.create_conversation(clinic_id, patient.phone, patient.id)
        reopen_if_closed(conversation)
        return conversation

    async def template(
        self, clinic_id: uuid.UUID, meta_name: str, patient: Patient, conversation: AiConversation
    ) -> WhatsAppTemplate | None:
        """Одобренный шаблон на языке пациента; нет на его языке — русский, потом любой."""
        lang = "ky" if conversation.lang is Lang.KG or patient.preferred_lang is Lang.KG else "ru"
        candidates = await self._repository.templates_by_name(clinic_id, meta_name)
        for template in candidates:
            if template.lang.lower().startswith(lang):
                return template
        return next((t for t in candidates if t.lang.lower().startswith("ru")), None) or (
            candidates[0] if candidates else None
        )

    async def send(
        self,
        clinic: Clinic,
        binding: ClinicWhatsApp,
        patient: Patient,
        conversation: AiConversation,
        template: WhatsAppTemplate,
        values: list[str],
        buttons: list[str] | None,
        now: datetime,
    ) -> Delivered:
        """Отправить шаблон и положить копию в диалог; ошибки отправки — наружу."""
        variables = values[: len(template.variables)]
        result = await self._wa.send_template(
            binding.phone_number_id,
            patient.phone,
            template.meta_name,
            template.lang,
            variables,
            buttons=buttons,
        )
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
        return Delivered(result.wa_message_id, conversation)
