"""Вебхуки подключённого через Embedded Signup номера: история, контакты, аккаунт.

* ``history`` — архив переписки из приложения WhatsApp Business. Пачки идут не
  по порядку (сначала свежие дни, потом старые), поэтому архив лежит отдельно
  от живой ленты (см. модель ``WhatsAppHistoryMessage``) и упорядочен по
  времени. Диалог, которого ещё не было, заводится закрытым: это архив, а не
  новый разговор, — ассистент и сводки его не трогают, живое сообщение
  пациента откроет его как обычно.
* ``smb_app_state_sync`` — адресная книга приложения: имена для списка диалогов.
* ``account_update`` — клиника отозвала доступ, номер ушёл из Cloud API или
  аккаунт удалён: привязка помечается «нужно переподключить».
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp.application.services.credentials import e164, flag_reconnect
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp

logger = structlog.get_logger(__name__)

#: События ``account_update``, после которых номер у нас больше не работает.
DISCONNECT_EVENTS: dict[str, str] = {
    "PARTNER_REMOVED": "partner_removed",
    "ACCOUNT_OFFBOARDED": "account_offboarded",
    "ACCOUNT_DELETED": "account_deleted",
}

#: Поле типа сообщения истории, у которого нет содержимого (медиа без файла).
_PLACEHOLDER = "media_placeholder"
_MEDIA_TYPES = ("image", "audio", "video", "document", "sticker")


def _ts(raw: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except TypeError, ValueError:
        return None


def _content(msg: dict[str, Any]) -> tuple[str, str | None]:
    """Текст и тип медиа сообщения истории."""
    kind = str(msg.get("type") or "")
    if kind == "text":
        return str((msg.get("text") or {}).get("body") or ""), None
    if kind in _MEDIA_TYPES:
        return str((msg.get(kind) or {}).get("caption") or ""), kind
    if kind == _PLACEHOLDER:
        return "", _PLACEHOLDER
    return "", kind or None


class AppWebhooks:
    """Обработчики трёх полей вебхука, которых нет у номера без приложения."""

    def __init__(self, session: AsyncSession, repository: WhatsAppRepository) -> None:
        self._session = session
        self._repository = repository

    async def _binding(self, value: dict[str, Any], field: str) -> ClinicWhatsApp | None:
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
        binding = (
            await self._repository.binding_by_phone_number_id(str(phone_number_id))
            if phone_number_id
            else None
        )
        if binding is None:
            logger.warning(
                "wa_app_webhook_unknown_number", field=field, phone_number_id=phone_number_id
            )
        return binding

    # ── История переписки ──────────────────────────────────────────────────

    async def history(self, value: dict[str, Any]) -> int:
        """Разложить пачку истории по архивам диалогов; вернуть число новых сообщений."""
        binding = await self._binding(value, "history")
        if binding is None:
            return 0
        errors = value.get("errors") or []
        if errors:
            # Клиника отказалась делиться историей в приложении (или Meta не смогла).
            first = errors[0] if isinstance(errors[0], dict) else {}
            details = (first.get("error_data") or {}).get("details")
            binding.history_error = str(details or first.get("message") or first.get("title"))[:300]
            logger.warning("wa_history_not_shared", clinic_id=str(binding.clinic_id))
        business = e164(binding.display_phone)
        imported = 0
        for chunk in value.get("history") or []:
            for thread in chunk.get("threads") or []:
                imported += await self._import_thread(binding, business, thread)
            progress = (chunk.get("metadata") or {}).get("progress")
            if isinstance(progress, int):
                binding.history_progress = max(binding.history_progress or 0, progress)
                if progress >= 100 and binding.history_synced_at is None:
                    binding.history_synced_at = datetime.now(UTC)
        return imported

    async def _import_thread(
        self, binding: ClinicWhatsApp, business: str, thread: dict[str, Any]
    ) -> int:
        if not thread.get("id"):
            return 0
        wa_phone = e164(str(thread["id"]))
        if wa_phone == business:
            return 0  # «чат с самим собой» в приложении — не диалог с пациентом
        conversation, created = await self._archive_conversation(binding.clinic_id, wa_phone)
        imported = 0
        newest: datetime | None = None
        for msg in thread.get("messages") or []:
            sent_at = _ts(msg.get("timestamp"))
            if not msg.get("id") or sent_at is None:
                continue
            body, media_type = _content(msg)
            direction = "out" if e164(str(msg.get("from") or "")) == business else "in"
            if await self._repository.insert_history(
                conversation,
                wa_message_id=str(msg["id"]),
                direction=direction,
                body=body,
                media_type=media_type,
                status=(msg.get("history_context") or {}).get("status"),
                sent_at=sent_at,
            ):
                imported += 1
                newest = sent_at if newest is None else max(newest, sent_at)
        if newest is not None:
            # Свежий архивный диалог встаёт в списке по своему последнему
            # сообщению, а не по моменту импорта; живой — не опускается ниже.
            conversation.last_msg_at = newest if created else max(conversation.last_msg_at, newest)
        return imported

    async def _archive_conversation(
        self, clinic_id: uuid.UUID, wa_phone: str
    ) -> tuple[AiConversation, bool]:
        conversation = await self._repository.conversation_by_phone(clinic_id, wa_phone)
        if conversation is not None:
            return conversation, False
        patient_id = await self._repository.patient_id_by_phone(wa_phone)
        conversation = await self._repository.create_conversation(clinic_id, wa_phone, patient_id)
        # Архив, а не разговор: без ассистента, сводок и «новых» в списке.
        conversation.status = AiConversationStatus.CLOSED
        return conversation, True

    # ── Адресная книга приложения ──────────────────────────────────────────

    async def contacts(self, value: dict[str, Any]) -> int:
        """Применить правки адресной книги; вернуть число применённых."""
        binding = await self._binding(value, "smb_app_state_sync")
        if binding is None:
            return 0
        applied = 0
        for item in value.get("state_sync") or []:
            if item.get("type") != "contact":
                continue
            contact = item.get("contact") or {}
            raw_phone = contact.get("phone_number")
            if not raw_phone:
                continue
            wa_phone = e164(str(raw_phone))
            if item.get("action") == "remove":
                await self._repository.delete_contact(binding.clinic_id, wa_phone)
                applied += 1
                continue
            full_name = str(contact.get("full_name") or contact.get("first_name") or "").strip()
            if not full_name:
                continue
            await self._repository.upsert_contact(
                binding.clinic_id,
                wa_phone,
                full_name=full_name,
                first_name=(contact.get("first_name") or None),
            )
            applied += 1
        return applied

    # ── Состояние аккаунта ─────────────────────────────────────────────────

    async def account_update(self, waba_id: str, value: dict[str, Any]) -> int:
        """Доступ отозван или номер ушёл из Cloud API — пометить привязки WABA."""
        event = str(value.get("event") or "")
        reason = DISCONNECT_EVENTS.get(event)
        if reason is None or not waba_id:
            logger.info("wa_account_update", waba_id=waba_id, account_event=event)
            return 0
        now = datetime.now(UTC)
        flagged = 0
        for binding in await self._repository.bindings_by_waba(waba_id):
            if binding.active and flag_reconnect(binding, reason, now):
                flagged += 1
        logger.warning("wa_account_disconnected", waba_id=waba_id, reason=reason, bindings=flagged)
        return flagged
