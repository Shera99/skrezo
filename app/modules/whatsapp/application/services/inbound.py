"""Разбор вебхука Meta: клиника → пациент → диалог → сообщение.

Работает в фоне (arq-задача) либо на месте, когда очередь недоступна, — вебхук
в любом случае уже ответил Meta 200. Повторная доставка того же события
безвредна: сообщения дедуплицируются по ``wa_message_id``.

Что обрабатывается:

* ``messages`` — входящие от пациентов (текст, медиа, кнопки);
* ``message_echoes`` — исходящие ЧЕЛОВЕКА из приложения WhatsApp Business
  (режим Coexistence, §2.3 ТЗ): код не предполагает, что мы единственный
  отправитель. Такие сообщения ложатся с ролью ``admin`` — в Ф2 это же
  событие ставит ассистента на паузу (§8.4);
* ``statuses`` (доставлено/прочитано) — игнорируются: телеметрия доставки
  пациенту в Ф1 не ведётся.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.whatsapp.application.services.media import save_media
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiMessageRole
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.domain.value_objects import normalize_phone
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

#: Типы сообщений Meta, несущие медиа-вложение.
_MEDIA_TYPES = ("image", "audio", "video", "document", "sticker")


def _normalize_phone(raw: str) -> str:
    """Номер из вебхука — к каноническому E.164 (как ``patient.phone``).

    Meta шлёт международный номер без ``+``. Каноникализация — той же
    функцией, что и у пациентов: иначе диалог и карточка не сматчатся из-за
    разницы форматов. Непарсибельный номер не роняет обработку — сохраняем
    как есть с ``+``: сообщение важнее идеального формата.
    """
    digits = raw.strip()
    candidate = digits if digits.startswith("+") else f"+{digits}"
    try:
        return normalize_phone(candidate)
    except AppError:
        return candidate


def _message_ts(msg: dict[str, Any]) -> datetime:
    """Момент сообщения по данным Meta; мусор в поле — текущее время."""
    try:
        return datetime.fromtimestamp(int(msg["timestamp"]), tz=UTC)
    except KeyError, TypeError, ValueError:
        return datetime.now(UTC)


def _extract_content(msg: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Вернуть (текст, media_type, media_id) сообщения любого типа.

    Текстом считается и подпись к медиа, и текст нажатой кнопки: для клиники
    это «что сказал пациент», а не технический тип payload'а.
    """
    kind = msg.get("type") or ""
    if kind == "text":
        return str(msg.get("text", {}).get("body") or ""), None, None
    if kind in _MEDIA_TYPES:
        media = msg.get(kind) or {}
        return str(media.get("caption") or ""), kind, media.get("id")
    if kind == "button":
        return str(msg.get("button", {}).get("text") or ""), None, None
    if kind == "interactive":
        reply = msg.get("interactive", {})
        title = reply.get("button_reply", {}).get("title") or reply.get("list_reply", {}).get(
            "title"
        )
        return str(title or ""), None, None
    # Незнакомый тип: сообщение фиксируем (пустым), тип остаётся в логе.
    logger.info("wa_inbound_unsupported_type", type=kind)
    return "", None, None


class InboundProcessor:
    """Обработчик payload'а вебхука целиком (все entry/changes)."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        client: WhatsAppCloudClient,
    ) -> None:
        self._session = session
        self._repository = repository
        self._client = client

    async def process(self, payload: dict[str, Any]) -> int:
        """Сохранить все сообщения из payload'а; вернуть число новых."""
        saved = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                saved += await self._process_value(value)
        await self._session.commit()
        return saved

    async def _process_value(self, value: dict[str, Any]) -> int:
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
        if not phone_number_id:
            return 0
        binding = await self._repository.binding_by_phone_number_id(str(phone_number_id))
        if binding is None:
            # Не наша клиника (или привязка выключена): Meta получает 200,
            # чтобы не копить ретраи, событие оседает только в логе.
            logger.warning("wa_inbound_unknown_number", phone_number_id=str(phone_number_id))
            return 0

        saved = 0
        for msg in value.get("messages") or []:
            saved += await self._save_inbound(binding.clinic_id, msg, AiMessageRole.PATIENT)
        # Coexistence: исходящие человека из приложения WhatsApp Business.
        for msg in value.get("message_echoes") or []:
            saved += await self._save_echo(binding.clinic_id, msg)
        return saved

    async def _conversation_for(self, clinic_id: Any, wa_phone: str) -> AiConversation:
        conversation = await self._repository.open_conversation(clinic_id, wa_phone)
        if conversation is None:
            patient_id = await self._repository.patient_id_by_phone(wa_phone)
            conversation = await self._repository.create_conversation(
                clinic_id, wa_phone, patient_id
            )
        return conversation

    async def _save_inbound(self, clinic_id: Any, msg: dict[str, Any], role: AiMessageRole) -> int:
        wa_message_id = msg.get("id")
        sender = msg.get("from")
        if not wa_message_id or not sender:
            return 0
        wa_phone = _normalize_phone(str(sender))
        ts = _message_ts(msg)
        body, media_type, media_id = _extract_content(msg)

        conversation = await self._conversation_for(clinic_id, wa_phone)
        message_id = await self._repository.insert_inbound(
            conversation,
            role=role,
            body=body,
            wa_message_id=str(wa_message_id),
            media_type=media_type,
            media_id=media_id,
            sent_at=ts,
        )
        if message_id is None:
            return 0  # повторная доставка вебхука — уже сохранено

        # Пациент мог появиться в базе между сообщениями — дополняем привязку.
        if conversation.patient_id is None:
            conversation.patient_id = await self._repository.patient_id_by_phone(wa_phone)
        conversation.last_msg_at = max(conversation.last_msg_at, ts)
        if role is AiMessageRole.PATIENT:
            last = conversation.last_patient_msg_at
            conversation.last_patient_msg_at = ts if last is None else max(last, ts)

        if media_id:
            await self._fetch_media(message_id, str(media_id))
        return 1

    async def _save_echo(self, clinic_id: Any, msg: dict[str, Any]) -> int:
        """Эхо исходящего человека: диалог ищется по ПОЛУЧАТЕЛЮ (``to``)."""
        wa_message_id = msg.get("id")
        recipient = msg.get("to")
        if not wa_message_id or not recipient:
            return 0
        wa_phone = _normalize_phone(str(recipient))
        ts = _message_ts(msg)
        body, media_type, media_id = _extract_content(msg)

        conversation = await self._conversation_for(clinic_id, wa_phone)
        message_id = await self._repository.insert_inbound(
            conversation,
            role=AiMessageRole.ADMIN,
            body=body,
            wa_message_id=str(wa_message_id),
            media_type=media_type,
            media_id=media_id,
            sent_at=ts,
        )
        if message_id is None:
            return 0
        conversation.last_msg_at = max(conversation.last_msg_at, ts)
        return 1

    async def _fetch_media(self, message_id: int, media_id: str) -> None:
        """Скачать медиа сразу: ссылка Meta живёт минуты, потом только id."""
        media = await self._client.fetch_media(media_id)
        if media is None:
            return
        path = await save_media(media.content, media.mime)
        await self._repository.set_media(message_id, path=path, mime=media.mime)
