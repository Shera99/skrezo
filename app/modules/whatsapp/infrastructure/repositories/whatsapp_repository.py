"""Репозиторий WhatsApp-канала: привязки номеров, диалоги, сообщения.

Каждый запрос по диалогам и сообщениям фильтруется ``clinic_id`` — изоляция
клиник (И7) проверяется здесь, а не в роутере.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp.domain.models import ConversationRow
from app.shared.db.enums import AiConversationStatus, AiMessageRole, WhatsAppMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.patient import Patient
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.whatsapp_cloud import TemplateInfo


class WhatsAppRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Привязка номера ─────────────────────────────────────────────────────

    async def binding_by_phone_number_id(
        self, phone_number_id: str, *, include_inactive: bool = False
    ) -> ClinicWhatsApp | None:
        """Привязка по идентификатору номера Meta (вебхук ищет только активные)."""
        stmt = select(ClinicWhatsApp).where(ClinicWhatsApp.phone_number_id == phone_number_id)
        if not include_inactive:
            stmt = stmt.where(ClinicWhatsApp.active.is_(True))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def binding_by_clinic(self, clinic_id: uuid.UUID) -> ClinicWhatsApp | None:
        return (
            await self._session.execute(
                select(ClinicWhatsApp).where(ClinicWhatsApp.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()

    async def clinic_exists(self, clinic_id: uuid.UUID) -> bool:
        return (
            await self._session.execute(select(Clinic.id).where(Clinic.id == clinic_id))
        ).scalar_one_or_none() is not None

    async def upsert_binding(
        self,
        clinic_id: uuid.UUID,
        *,
        phone_number_id: str,
        display_phone: str,
        waba_id: str | None,
        mode: WhatsAppMode,
        active: bool,
    ) -> ClinicWhatsApp:
        """Создать или обновить привязку клиники (одна строка на клинику)."""
        binding = await self.binding_by_clinic(clinic_id)
        if binding is None:
            binding = ClinicWhatsApp(clinic_id=clinic_id, phone_number_id=phone_number_id)
            self._session.add(binding)
        binding.phone_number_id = phone_number_id
        binding.display_phone = display_phone
        binding.waba_id = waba_id
        binding.mode = mode
        binding.active = active
        await self._session.flush()
        return binding

    # ── Пациент ─────────────────────────────────────────────────────────────

    async def patient_id_by_phone(self, phone: str) -> uuid.UUID | None:
        """Глобальный пациент по номеру E.164 (телефон уникален на платформу)."""
        return (
            await self._session.execute(select(Patient.id).where(Patient.phone == phone))
        ).scalar_one_or_none()

    async def patient_name(self, patient_id: uuid.UUID | None) -> str | None:
        if patient_id is None:
            return None
        row = (
            await self._session.execute(
                select(func.concat_ws(" ", Patient.last_name, Patient.first_name)).where(
                    Patient.id == patient_id
                )
            )
        ).scalar_one_or_none()
        return (row or "").strip() or None

    # ── Диалоги ─────────────────────────────────────────────────────────────

    async def open_conversation(self, clinic_id: uuid.UUID, wa_phone: str) -> AiConversation | None:
        """Незакрытый диалог пары (клиника, номер) — active ЛИБО escalated.

        Только по ``active`` искать нельзя: у эскалированного диалога (Ф2)
        новое входящее заводило бы второй диалог параллельно живому.
        """
        return (
            await self._session.execute(
                select(AiConversation).where(
                    AiConversation.clinic_id == clinic_id,
                    AiConversation.wa_phone == wa_phone,
                    AiConversation.status != AiConversationStatus.CLOSED,
                )
            )
        ).scalar_one_or_none()

    async def create_conversation(
        self, clinic_id: uuid.UUID, wa_phone: str, patient_id: uuid.UUID | None
    ) -> AiConversation:
        conversation = AiConversation(
            clinic_id=clinic_id,
            wa_phone=wa_phone,
            patient_id=patient_id,
            status=AiConversationStatus.ACTIVE,
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def conversation(
        self, clinic_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> AiConversation | None:
        conv = await self._session.get(AiConversation, conversation_id)
        return conv if conv is not None and conv.clinic_id == clinic_id else None

    async def list_page(
        self,
        clinic_id: uuid.UUID,
        *,
        status: AiConversationStatus | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ConversationRow], int]:
        # Превью — последнее сообщение диалога; курсор по id (см. модель).
        last_body = (
            select(AiMessage.body)
            .where(AiMessage.conversation_id == AiConversation.id)
            .order_by(AiMessage.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(
                AiConversation.id,
                AiConversation.wa_phone,
                AiConversation.patient_id,
                func.concat_ws(" ", Patient.last_name, Patient.first_name).label("patient_name"),
                AiConversation.lang,
                AiConversation.status,
                AiConversation.last_msg_at,
                AiConversation.last_patient_msg_at,
                last_body.label("last_body"),
            )
            .outerjoin(Patient, Patient.id == AiConversation.patient_id)
            .where(AiConversation.clinic_id == clinic_id)
        )
        if status is not None:
            stmt = stmt.where(AiConversation.status == status)
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                stmt.order_by(AiConversation.last_msg_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        items = [
            ConversationRow(
                id=r[0],
                wa_phone=r[1],
                patient_id=r[2],
                patient_name=((r[3] or "").strip() or None),
                lang=r[4],
                status=r[5],
                last_msg_at=r[6],
                last_patient_msg_at=r[7],
                last_body=r[8],
            )
            for r in rows
        ]
        return items, total

    async def close_stale(self, cutoff: datetime) -> int:
        """Закрыть диалоги без сообщений с ``cutoff``; вернуть число закрытых.

        Закрываются и ``escalated``: сутки тишины после вмешательства человека —
        разговор кончился (§8.4 ТЗ ассистента). Резюме диалога появится в Ф2.
        """
        result = await self._session.execute(
            update(AiConversation)
            .where(
                AiConversation.status != AiConversationStatus.CLOSED,
                AiConversation.last_msg_at < cutoff,
            )
            .values(status=AiConversationStatus.CLOSED)
            .returning(AiConversation.id)
        )
        return len(list(result.scalars()))

    # ── Сообщения ───────────────────────────────────────────────────────────

    async def insert_inbound(
        self,
        conversation: AiConversation,
        *,
        role: AiMessageRole,
        body: str,
        wa_message_id: str,
        media_type: str | None,
        media_id: str | None,
        sent_at: datetime,
    ) -> int | None:
        """Вставить сообщение вебхука; ``None`` — дубль повторной доставки."""
        stmt = (
            pg_insert(AiMessage)
            .values(
                conversation_id=conversation.id,
                clinic_id=conversation.clinic_id,
                role=role,
                body=body,
                wa_message_id=wa_message_id,
                media_type=media_type,
                media_id=media_id,
                sent_at=sent_at,
            )
            .on_conflict_do_nothing(index_elements=["wa_message_id"])
            .returning(AiMessage.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_message(
        self,
        conversation: AiConversation,
        *,
        role: AiMessageRole,
        body: str,
        wa_message_id: str | None,
        sent_at: datetime | None,
    ) -> AiMessage:
        message = AiMessage(
            conversation_id=conversation.id,
            clinic_id=conversation.clinic_id,
            role=role,
            body=body,
            wa_message_id=wa_message_id,
            sent_at=sent_at,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def set_media(self, message_id: int, *, path: str, mime: str) -> None:
        await self._session.execute(
            update(AiMessage)
            .where(AiMessage.id == message_id)
            .values(media_path=path, media_mime=mime)
        )

    async def messages_page(
        self, conversation_id: uuid.UUID, *, before_id: int | None, limit: int
    ) -> list[AiMessage]:
        """Последние сообщения диалога (новые сверху), курсор «старше id»."""
        stmt = select(AiMessage).where(AiMessage.conversation_id == conversation_id)
        if before_id is not None:
            stmt = stmt.where(AiMessage.id < before_id)
        rows = (
            await self._session.execute(stmt.order_by(AiMessage.id.desc()).limit(limit))
        ).scalars()
        return list(rows)

    async def message(
        self, clinic_id: uuid.UUID, conversation_id: uuid.UUID, message_id: int
    ) -> AiMessage | None:
        return (
            await self._session.execute(
                select(AiMessage).where(
                    AiMessage.id == message_id,
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.clinic_id == clinic_id,
                )
            )
        ).scalar_one_or_none()

    # ── Шаблоны ─────────────────────────────────────────────────────────────

    async def replace_templates(
        self, clinic_id: uuid.UUID, items: list[TemplateInfo]
    ) -> None:
        """Привести каталог шаблонов клиники к списку из кабинета Meta.

        Полная сверка, а не только upsert: шаблон, удалённый в кабинете,
        должен пропасть и у нас — иначе клиника отправляет в несуществующее
        и получает молчаливый отказ Graph API. Общие шаблоны платформы
        (``clinic_id IS NULL``) сверка не трогает.
        """
        keys = [(t.meta_name, t.lang) for t in items]
        stale = delete(WhatsAppTemplate).where(WhatsAppTemplate.clinic_id == clinic_id)
        if keys:
            stale = stale.where(
                tuple_(WhatsAppTemplate.meta_name, WhatsAppTemplate.lang).not_in(keys)
            )
        await self._session.execute(stale)

        existing = {
            (t.meta_name, t.lang): t
            for t in (
                await self._session.execute(
                    select(WhatsAppTemplate).where(WhatsAppTemplate.clinic_id == clinic_id)
                )
            ).scalars()
        }
        for info in items:
            row = existing.get((info.meta_name, info.lang))
            if row is None:
                row = WhatsAppTemplate(
                    clinic_id=clinic_id, meta_name=info.meta_name, lang=info.lang
                )
                self._session.add(row)
            row.category = info.category
            row.body_preview = info.body_preview
            row.variables = list(info.variables)
            row.status = info.status
        await self._session.flush()

    async def list_templates(self, clinic_id: uuid.UUID) -> list[WhatsAppTemplate]:
        """Шаблоны, доступные клинике: свои + общие платформы."""
        rows = (
            await self._session.execute(
                select(WhatsAppTemplate)
                .where(
                    or_(
                        WhatsAppTemplate.clinic_id == clinic_id,
                        WhatsAppTemplate.clinic_id.is_(None),
                    )
                )
                .order_by(WhatsAppTemplate.meta_name, WhatsAppTemplate.lang)
            )
        ).scalars()
        return list(rows)

    async def template(
        self, clinic_id: uuid.UUID, template_id: uuid.UUID
    ) -> WhatsAppTemplate | None:
        """Шаблон по id — свой либо общий (чужой клиники не отдаём)."""
        return (
            await self._session.execute(
                select(WhatsAppTemplate).where(
                    WhatsAppTemplate.id == template_id,
                    or_(
                        WhatsAppTemplate.clinic_id == clinic_id,
                        WhatsAppTemplate.clinic_id.is_(None),
                    ),
                )
            )
        ).scalar_one_or_none()
