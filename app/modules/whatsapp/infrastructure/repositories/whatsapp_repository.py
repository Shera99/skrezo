"""Репозиторий WhatsApp-канала: привязки номеров, диалоги, сообщения.

Каждый запрос по диалогам и сообщениям фильтруется ``clinic_id`` — изоляция
клиник (И7) проверяется здесь, а не в роутере.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, true, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp.domain.models import ConversationCounters, ConversationRow
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
        filter_: str = "all",
        query: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[ConversationRow], int]:
        # Последнее сообщение — LATERAL: нужны сразу роль, текст и медиа,
        # а четыре коррелированных подзапроса ради одной строки — расточительно.
        last = (
            select(
                AiMessage.role.label("role"),
                AiMessage.body.label("body"),
                AiMessage.media_type.label("media_type"),
                AiMessage.media_duration_sec.label("duration"),
            )
            .where(AiMessage.conversation_id == AiConversation.id)
            .order_by(AiMessage.id.desc())
            .limit(1)
            .lateral("last_message")
        )
        patient_name = func.concat_ws(" ", Patient.last_name, Patient.first_name)
        stmt = (
            select(
                AiConversation.id,
                AiConversation.wa_phone,
                AiConversation.patient_id,
                patient_name.label("patient_name"),
                AiConversation.lang,
                AiConversation.status,
                AiConversation.last_msg_at,
                AiConversation.last_patient_msg_at,
                last.c.body,
                last.c.role,
                last.c.media_type,
                last.c.duration,
                self._unread_subquery().label("unread"),
                self._pending_draft_subquery().label("pending_draft_id"),
            )
            .select_from(AiConversation)
            .outerjoin(Patient, Patient.id == AiConversation.patient_id)
            .outerjoin(last, true())
            .where(AiConversation.clinic_id == clinic_id)
        )
        if status is not None:
            stmt = stmt.where(AiConversation.status == status)
        if filter_ == "awaiting":
            stmt = stmt.where(
                last.c.role == AiMessageRole.PATIENT,
                AiConversation.status != AiConversationStatus.CLOSED,
            )
        elif filter_ == "needs_human":
            stmt = stmt.where(
                or_(
                    AiConversation.status == AiConversationStatus.ESCALATED,
                    self._pending_draft_subquery().is_not(None),
                )
            )
        if query:
            # Поиск по номеру, имени и тексту; текст — через триграммный индекс.
            pattern = f"%{query}%"
            in_messages = (
                select(AiMessage.id)
                .where(
                    AiMessage.conversation_id == AiConversation.id,
                    AiMessage.body.ilike(pattern),
                )
                .exists()
            )
            stmt = stmt.where(
                or_(
                    AiConversation.wa_phone.ilike(pattern),
                    patient_name.ilike(pattern),
                    in_messages,
                )
            )
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
                last_role=r[9],
                last_media_type=r[10],
                last_media_duration_sec=r[11],
                unread_count=int(r[12] or 0),
                pending_draft_id=r[13],
            )
            for r in rows
        ]
        return items, total

    @staticmethod
    def _unread_subquery() -> Any:
        """Сообщения пациента новее общей отметки прочтения диалога."""
        return (
            select(func.count())
            .select_from(AiMessage)
            .where(
                AiMessage.conversation_id == AiConversation.id,
                AiMessage.role == AiMessageRole.PATIENT,
                AiMessage.id > func.coalesce(AiConversation.read_message_id, 0),
            )
            .scalar_subquery()
        )

    @staticmethod
    def _pending_draft_subquery() -> Any:
        """Свежий черновик ассистента, ждущий решения (режим «Проверяю»)."""
        return (
            select(AiMessage.id)
            .where(
                AiMessage.conversation_id == AiConversation.id,
                AiMessage.review == "pending",
            )
            .order_by(AiMessage.id.desc())
            .limit(1)
            .scalar_subquery()
        )

    async def counters(self, clinic_id: uuid.UUID) -> ConversationCounters:
        """Числа вкладок и бейдж одним запросом по диалогам клиники."""
        last_role = (
            select(AiMessage.role)
            .where(AiMessage.conversation_id == AiConversation.id)
            .order_by(AiMessage.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        per_conversation = (
            select(
                AiConversation.status.label("status"),
                last_role.label("last_role"),
                self._pending_draft_subquery().label("pending_draft_id"),
                self._unread_subquery().label("unread"),
            )
            .where(AiConversation.clinic_id == clinic_id)
            .subquery()
        )
        open_ = per_conversation.c.status != AiConversationStatus.CLOSED
        row = (
            await self._session.execute(
                select(
                    func.count().filter(open_),
                    func.count().filter(
                        open_, per_conversation.c.last_role == AiMessageRole.PATIENT
                    ),
                    func.count().filter(
                        or_(
                            per_conversation.c.status == AiConversationStatus.ESCALATED,
                            per_conversation.c.pending_draft_id.is_not(None),
                        )
                    ),
                    func.count().filter(per_conversation.c.unread > 0),
                )
            )
        ).one()
        return ConversationCounters(
            total=int(row[0]),
            awaiting=int(row[1]),
            needs_human=int(row[2]),
            unread=int(row[3]),
        )

    async def mark_read(
        self, conversation_id: uuid.UUID, clinic_id: uuid.UUID, *, up_to_message_id: int | None
    ) -> int | None:
        """Сдвинуть отметку прочтения вперёд (никогда назад); ``None`` — диалог не найден."""
        latest = (
            select(func.max(AiMessage.id))
            .where(AiMessage.conversation_id == conversation_id)
            .scalar_subquery()
        )
        target = func.coalesce(up_to_message_id, latest, 0)
        result = await self._session.execute(
            update(AiConversation)
            .where(AiConversation.id == conversation_id, AiConversation.clinic_id == clinic_id)
            .values(
                read_message_id=func.greatest(
                    func.coalesce(AiConversation.read_message_id, 0), target
                )
            )
            .returning(AiConversation.read_message_id)
        )
        return result.scalar_one_or_none()

    async def conversation_exists(self, conversation_id: uuid.UUID, clinic_id: uuid.UUID) -> bool:
        found = (
            await self._session.execute(
                select(AiConversation.id).where(
                    AiConversation.id == conversation_id,
                    AiConversation.clinic_id == clinic_id,
                )
            )
        ).first()
        return found is not None

    async def unread_count(self, conversation_id: uuid.UUID) -> int:
        count = (
            await self._session.execute(
                select(func.count())
                .select_from(AiMessage)
                .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
                .where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.role == AiMessageRole.PATIENT,
                    AiMessage.id > func.coalesce(AiConversation.read_message_id, 0),
                )
            )
        ).scalar_one()
        return int(count)

    async def message_by_wa_id(self, wa_message_id: str) -> AiMessage | None:
        """Наше сообщение по идентификатору Меты (``wamid…``); индекс уникальный."""
        return (
            await self._session.execute(
                select(AiMessage).where(AiMessage.wa_message_id == wa_message_id)
            )
        ).scalar_one_or_none()

    async def last_message_role(self, conversation_id: uuid.UUID) -> AiMessageRole | None:
        """Кто написал последним — «ждёт ли пациент ответа» одним запросом."""
        return (
            await self._session.execute(
                select(AiMessage.role)
                .where(AiMessage.conversation_id == conversation_id)
                .order_by(AiMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def close_stale(self, cutoff: datetime) -> list[uuid.UUID]:
        """Закрыть диалоги без сообщений с ``cutoff``; вернуть их идентификаторы.

        Закрываются и ``escalated``: сутки тишины после вмешательства человека —
        разговор кончился (§8.4 ТЗ ассистента). По возвращённым id фоновая
        задача строит резюме (§3.3).
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
        return list(result.scalars())

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

    async def set_media(
        self, message_id: int, *, path: str, mime: str, duration_sec: int | None = None
    ) -> None:
        await self._session.execute(
            update(AiMessage)
            .where(AiMessage.id == message_id)
            .values(media_path=path, media_mime=mime, media_duration_sec=duration_sec)
        )

    async def set_transcript(self, message_id: int, *, text: str, confidence: float) -> None:
        """Расшифровка голосового и уверенность в ней (§11)."""
        await self._session.execute(
            update(AiMessage)
            .where(AiMessage.id == message_id)
            .values(transcript=text, transcript_confidence=confidence)
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

    async def replace_templates(self, clinic_id: uuid.UUID, items: list[TemplateInfo]) -> None:
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
