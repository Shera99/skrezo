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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.push.application.outbox import emit_event
from app.modules.whatsapp.application.services.media import save_media
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiMessageRole
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.domain.value_objects import normalize_phone
from app.shared.realtime import publish_realtime
from app.shared.speech import SpeechError, SpeechProvider
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


def _confirm_button_id(msg: dict[str, Any]) -> str | None:
    """id нажатой кнопки подтверждения записи (``hold:<uuid>``) либо ``None``."""
    reply = (msg.get("interactive") or {}).get("button_reply") or {}
    raw = str(reply.get("id") or "")
    return raw if raw.startswith("hold:") else None


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


@dataclass(frozen=True, slots=True)
class InboundRef:
    """Ссылка на сохранённое ВХОДЯЩЕЕ пациента — для запуска ассистента (Ф2)."""

    conversation_id: uuid.UUID
    message_id: int


class InboundProcessor:
    """Обработчик payload'а вебхука целиком (все entry/changes)."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        client: WhatsAppCloudClient,
        speech: SpeechProvider | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._client = client
        # ``None`` — распознавание не настроено: голосовое сохраняется как есть
        # и уходит человеку (§11), выдуманной расшифровки не бывает.
        self._speech = speech

    async def process(self, payload: dict[str, Any]) -> list[InboundRef]:
        """Сохранить все сообщения payload'а; вернуть ссылки на ВХОДЯЩИЕ пациентов.

        По ссылкам вызывающий запускает ассистента (Ф2): вебхук — фоновой
        задачей, симулятор — на месте. Эхо и статусы ссылок не порождают.
        """
        refs: list[InboundRef] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                refs.extend(await self._process_value(value))
        await self._session.commit()
        return refs

    async def _process_value(self, value: dict[str, Any]) -> list[InboundRef]:
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
        if not phone_number_id:
            return []
        binding = await self._repository.binding_by_phone_number_id(str(phone_number_id))
        if binding is None:
            # Не наша клиника (или привязка выключена): Meta получает 200,
            # чтобы не копить ретраи, событие оседает только в логе.
            logger.warning("wa_inbound_unknown_number", phone_number_id=str(phone_number_id))
            return []

        refs: list[InboundRef] = []
        for msg in value.get("messages") or []:
            ref = await self._save_inbound(binding.clinic_id, msg, AiMessageRole.PATIENT)
            if ref is not None:
                refs.append(ref)
        # Coexistence: исходящие человека из приложения WhatsApp Business.
        for msg in value.get("message_echoes") or []:
            await self._save_echo(binding.clinic_id, msg)
        return refs

    async def _conversation_for(self, clinic_id: Any, wa_phone: str) -> AiConversation:
        conversation = await self._repository.open_conversation(clinic_id, wa_phone)
        if conversation is None:
            patient_id = await self._repository.patient_id_by_phone(wa_phone)
            conversation = await self._repository.create_conversation(
                clinic_id, wa_phone, patient_id
            )
        return conversation

    async def _save_inbound(
        self, clinic_id: Any, msg: dict[str, Any], role: AiMessageRole
    ) -> InboundRef | None:
        wa_message_id = msg.get("id")
        sender = msg.get("from")
        if not wa_message_id or not sender:
            return None
        wa_phone = _normalize_phone(str(sender))
        ts = _message_ts(msg)
        body, media_type, media_id = _extract_content(msg)

        conversation = await self._conversation_for(clinic_id, wa_phone)

        # Кнопка подтверждения записи — детерминированный путь МИМО модели
        # (И3): нажатие несёт confirmation_id, обработчик сам создаёт приём.
        button_id = _confirm_button_id(msg)
        if button_id is not None:
            saved = await self._repository.insert_inbound(
                conversation,
                role=role,
                body=body or "✔",
                wa_message_id=str(wa_message_id),
                media_type=None,
                media_id=None,
                sent_at=ts,
            )
            if saved is None:
                return None  # повтор вебхука — подтверждение уже обработано
            conversation.last_msg_at = max(conversation.last_msg_at, ts)
            conversation.last_patient_msg_at = ts
            # Фиксация до confirm: дедуп wamid переживает rollback гонки за слот.
            await self._session.commit()
            await self._confirm_booking(conversation, button_id)
            return None  # ассистенту этот ход не передаётся
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
            return None  # повторная доставка вебхука — уже сохранено

        # Пациент мог появиться в базе между сообщениями — дополняем привязку.
        if conversation.patient_id is None:
            conversation.patient_id = await self._repository.patient_id_by_phone(wa_phone)
        conversation.last_msg_at = max(conversation.last_msg_at, ts)
        if role is AiMessageRole.PATIENT:
            last = conversation.last_patient_msg_at
            conversation.last_patient_msg_at = ts if last is None else max(last, ts)

        if media_id:
            await self._fetch_media(message_id, str(media_id), media_type)
        # Сигнал открытым экранам: чат перечитает диалог (Ф4, contract §9).
        await publish_realtime(
            self._session,
            conversation.clinic_id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": "in"},
        )
        # Событие §4 «новое сообщение» — админ узнаёт из инбокса/сводки (low).
        await emit_event(
            self._session,
            clinic_id=conversation.clinic_id,
            type_="conversation.inbound",
            entity_type="conversation",
            entity_id=conversation.id,
        )
        return InboundRef(conversation_id=conversation.id, message_id=message_id)

    async def _save_echo(self, clinic_id: Any, msg: dict[str, Any]) -> None:
        """Эхо исходящего человека: диалог ищется по ПОЛУЧАТЕЛЮ (``to``)."""
        wa_message_id = msg.get("id")
        recipient = msg.get("to")
        if not wa_message_id or not recipient:
            return
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
            return
        conversation.last_msg_at = max(conversation.last_msg_at, ts)
        await publish_realtime(
            self._session,
            conversation.clinic_id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": "out"},
        )

    async def _confirm_booking(self, conversation: AiConversation, button_id: str) -> None:
        """Создать приём по кнопке и ответить пациенту шаблоном (не моделью)."""
        from app.modules.ai.application.booking import BookingService

        # Локальные копии: внутри confirm возможен rollback (гонка за слот),
        # который протухает загруженные ORM-объекты — доступ к их атрибутам
        # после него падал бы MissingGreenlet.
        clinic_id = conversation.clinic_id
        booking = BookingService(self._session)
        reply_text = await booking.confirm(conversation, button_id)
        await self._session.refresh(conversation)
        binding = await self._repository.binding_by_clinic(clinic_id)
        wa_message_id: str | None = None
        sent_at = None
        if binding is not None and binding.active:
            try:
                sent = await self._client.send_text(
                    binding.phone_number_id, conversation.wa_phone, reply_text
                )
                wa_message_id = sent.wa_message_id
                sent_at = datetime.now(UTC)
            except Exception as exc:  # ответ пациенту — не повод терять приём
                logger.warning("ai_booking_reply_failed", error=str(exc))
        await self._repository.add_message(
            conversation,
            role=AiMessageRole.SYSTEM,
            body=reply_text,
            wa_message_id=wa_message_id,
            sent_at=sent_at,
        )
        await publish_realtime(
            self._session,
            conversation.clinic_id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": "out"},
        )

    async def _fetch_media(
        self, message_id: int, media_id: str, media_type: str | None
    ) -> None:
        """Скачать медиа сразу: ссылка Meta живёт минуты, потом только id."""
        media = await self._client.fetch_media(media_id)
        if media is None:
            return
        path = await save_media(media.content, media.mime)
        await self._repository.set_media(message_id, path=path, mime=media.mime)
        if media_type == "audio":
            await self._transcribe(message_id, media.content, media.mime)

    async def _transcribe(self, message_id: int, audio: bytes, mime: str) -> None:
        """Расшифровать голосовое (§11); отказ провайдера — не потеря сообщения.

        Уверенность сохраняется рядом с текстом: решение «верить или звать
        человека» принимает конвейер ассистента, а не этот обработчик.
        """
        if self._speech is None:
            return
        try:
            result = await self._speech.transcribe(audio, mime)
        except SpeechError as exc:
            logger.warning("wa_transcribe_failed", error=str(exc))
            return
        await self._repository.set_transcript(
            message_id, text=result.text, confidence=result.confidence
        )
