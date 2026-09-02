"""Оркестратор ответа ассистента: контекст → модель ⇄ инструменты → валидатор → режим.

Поток §2.1 (без медфильтра и ASR — следующие инкременты). Дисциплина ошибок —
§14: один повтор при сбое модели, затем эскалация; недоступный провайдер —
полная эскалация, бот молчит. Тишина лучше выдумки.

Режимы (§13.1): ``shadow`` — ответ сохраняется с ``sent_at = null`` («я бы
ответил так»), пациенту не уходит; ``auto`` — уходит всё, что прошло
валидатор; ``assisted`` в этом инкременте ведёт себя как shadow (зелёная зона
наружу — вместе с медфильтром). Пока диалог ``escalated`` — бот молчит.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ai.application.booking import BookingService
from app.modules.ai.application.tools import TOOL_DECLS, ToolContext, ToolExecutor
from app.modules.ai.domain.facts import TurnFacts
from app.modules.ai.domain.medical import MedicalKind, MedicalVerdict, classify
from app.modules.ai.domain.prompt import ClinicCard, build_system_prompt
from app.modules.ai.domain.reply import BotReply, ReplyParseError, parse_reply
from app.modules.ai.domain.texts import medical_text, service_text
from app.modules.ai.domain.validator import Verdict, VerdictAction, validate
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.push.application.outbox import emit_event
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.ai import AiMessagePart, AiProvider, AiProviderError
from app.shared.db.enums import AiConversationStatus, AiMessageRole, AiMode, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

logger = structlog.get_logger(__name__)

#: Шаблон «уточню у администратора» — двуязычный, моделью НЕ переводится (§10.2).
_ESCALATION_TEXTS: dict[str, str] = {
    "ru": "Уточню у администратора и вернусь с ответом.",
    "ky": "Администратордон тактап, жооп берем.",
}

#: Язык спеки (ru|ky) → наш Lang для conversation/patient.
_SPEC_TO_LANG = {"ru": Lang.RU, "ky": Lang.KG}


@dataclass(frozen=True, slots=True)
class ReplyOutcome:
    """Что произошло с ходом ассистента (для симулятора и тестов)."""

    status: str  # sent | shadow | escalated | skipped
    message_id: int | None = None
    reply: str | None = None
    escalation_reason: str | None = None


class ProcessAiReplyService:
    def __init__(
        self,
        session: AsyncSession,
        provider: AiProvider | None,
        wa_client: WhatsAppCloudClient,
    ) -> None:
        self._session = session
        self._provider = provider
        self._wa = wa_client
        self._repository = AiRepository(session)
        self._whatsapp = WhatsAppRepository(session)

    async def execute(self, conversation_id: uuid.UUID) -> ReplyOutcome:
        """Ответить на последнее сообщение пациента в диалоге (если положено)."""
        conversation = await self._session.get(AiConversation, conversation_id)
        if conversation is None:
            return ReplyOutcome(status="skipped")
        clinic = await self._session.get(Clinic, conversation.clinic_id)
        if clinic is None or clinic.ai_mode is AiMode.OFF:
            return ReplyOutcome(status="skipped")
        if conversation.status is not AiConversationStatus.ACTIVE:
            # escalated: человек ведёт разговор, бот молчит до возврата (§8.4).
            return ReplyOutcome(status="skipped")
        # §9: медицинский фильтр — детерминированно и ДО модели. Стоит выше
        # проверки провайдера намеренно: неотложное состояние пациента не
        # должно зависеть от того, доступна ли сейчас модель.
        medical = classify(await self._last_patient_text(conversation))
        if medical.kind is MedicalKind.URGENT:
            return await self._urgent(conversation, clinic, medical)
        if medical.kind is MedicalKind.COMPLAINT:
            return await self._complaint(conversation, clinic, medical)

        # §14: поток сообщений от одного пациента. Молча игнорировать нельзя —
        # человек решит, что его не слышат; отвечаем один раз и зовём человека.
        if await self._rate_limited(conversation):
            return await self._too_many(conversation, clinic)

        if self._provider is None:
            logger.warning("ai_provider_missing", clinic_id=str(clinic.id))
            return ReplyOutcome(status="skipped")

        # Язык фиксируется первым ПРОШЕДШИМ ответом бота (§10.2): до него
        # модель распознаёт язык сама, и V07 не применяется.
        first_reply = await self._repository.bot_replies_count(conversation.id) == 0
        lang: str | None = None if first_reply else (
            "ky" if conversation.lang is Lang.KG else "ru"
        )
        system = await self._build_system(clinic, lang)
        parts = await self._history_parts(conversation)
        executor = ToolExecutor(self._session, self._repository)
        binding = await self._whatsapp.binding_by_clinic(clinic.id)
        tool_context = ToolContext(
            conversation=conversation,
            clinic=clinic,
            wa_client=self._wa,
            binding_phone_number_id=(
                binding.phone_number_id if binding is not None and binding.active else None
            ),
        )

        escalate_call: dict[str, Any] | None = None
        raw_text: str | None = None
        model_name = self._provider.model_name
        try:
            for _ in range(settings.ai_max_tool_calls + 1):
                result = await self._provider.complete(
                    system=system,
                    parts=parts,
                    tools=TOOL_DECLS,
                    temperature=settings.ai_temperature,
                    max_tokens=settings.ai_max_tokens,
                )
                if result.tool_call is None:
                    raw_text = result.text
                    break
                call = result.tool_call
                if call.name == "escalate":
                    escalate_call = call.args
                    break
                outcome = await executor.execute(
                    conversation.id, tool_context, call.name, call.args
                )
                parts.append(
                    AiMessagePart(
                        role="model",
                        tool_name=call.name,
                        tool_args=call.args,
                        tool_signature=call.signature,
                    )
                )
                parts.append(
                    AiMessagePart(role="tool", tool_name=call.name, tool_result=outcome)
                )
            else:
                # Потолок вызовов — модель зациклилась на инструментах.
                return await self._escalate(
                    conversation, clinic, "tool_error", {"note": "tool loop limit"}
                )
        except AiProviderError as exc:
            logger.warning("ai_provider_error", error=str(exc))
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": f"provider: {exc}"}
            )

        if escalate_call is not None:
            return await self._escalate(
                conversation,
                clinic,
                str(escalate_call.get("reason") or "patient_asked"),
                {"note": escalate_call.get("note")},
            )
        if raw_text is None:
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": "модель не дала текста"}
            )

        facts = await self._turn_facts(clinic, system, parts)
        reply, verdict = await self._parse_and_validate(
            conversation, system, parts, raw_text, lang, facts
        )
        if reply is None or verdict is None:
            return await self._escalate(
                conversation,
                clinic,
                "validator_blocked",
                # Хвост сырого текста — иначе «непарсибельный» нечем разбирать.
                {"note": "непарсибельный ответ", "raw": (raw_text or "")[:300]},
            )
        if verdict.action is VerdictAction.BLOCK:
            return await self._blocked(conversation, clinic, reply, verdict, model_name)
        if verdict.action is VerdictAction.ESCALATE:
            return await self._needs_human(conversation, clinic, reply, verdict, model_name)
        return await self._deliver(conversation, clinic, reply, verdict, model_name)

    # ── Контекст ────────────────────────────────────────────────────────────

    async def _build_system(self, clinic: Clinic, lang: str | None) -> str:
        glossary = await self._repository.approved_glossary(clinic.id)
        kb = await self._repository.kb_for_prompt(clinic.id, lang or "ru")
        card = ClinicCard(
            name=clinic.name,
            address=clinic.address,
            phone=clinic.business_phone,
            working_hours=None,  # человекочитаемый график — вместе с KB-редактором
        )
        return build_system_prompt(card, lang, glossary, kb)

    async def _history_parts(self, conversation: AiConversation) -> list[AiMessagePart]:
        """Последние 20 сообщений диалога → реплики хода (§4.1, блок 7)."""
        rows = (
            await self._session.execute(
                select(AiMessage)
                .where(AiMessage.conversation_id == conversation.id)
                .order_by(AiMessage.id.desc())
                .limit(20)
            )
        ).scalars()
        parts: list[AiMessagePart] = []
        for message in reversed(list(rows)):
            if message.role is AiMessageRole.PATIENT:
                parts.append(AiMessagePart(role="user", text=message.body))
            elif (
                message.role in (AiMessageRole.BOT, AiMessageRole.ADMIN)
                and message.sent_at is not None
            ):
                # Отправленные ответы бота и человека — «наша сторона» диалога:
                # модель обязана видеть, что клиника уже сказала пациенту.
                parts.append(AiMessagePart(role="model", text=message.body))
        if not parts or parts[-1].role != "user":
            parts.append(AiMessagePart(role="user", text=""))
        return parts

    # ── Разбор и перегенерация ──────────────────────────────────────────────

    async def _parse_and_validate(
        self,
        conversation: AiConversation,
        system: str,
        parts: list[AiMessagePart],
        raw_text: str,
        lang: str | None,
        facts: TurnFacts,
    ) -> tuple[BotReply | None, Verdict | None]:
        """Разобрать ответ; V07/V08 и битый JSON — одна перегенерация (§7, §14)."""
        for attempt in range(2):
            try:
                reply = parse_reply(raw_text)
            except ReplyParseError as exc:
                if attempt == 1:
                    return None, None
                raw_text = await self._retry(
                    system, parts, raw_text, f"Ответ не JSON ({exc}). Верни строго один JSON."
                )
                continue
            verdict = validate(reply, lang, facts)
            if verdict.action is not VerdictAction.REGENERATE or attempt == 1:
                if verdict.action is VerdictAction.REGENERATE:
                    # Повтор не помог — по §7 дальше эскалация.
                    return reply, Verdict(VerdictAction.ESCALATE, verdict.codes, verdict.notes)
                return reply, verdict
            instruction = (
                f"Ответь на языке диалога ({lang or 'ru'}) и не длиннее 1000 символов. "
                "Верни строго один JSON."
            )
            raw_text = await self._retry(system, parts, raw_text, instruction)
        return None, None

    async def _retry(
        self, system: str, parts: list[AiMessagePart], previous: str, instruction: str
    ) -> str:
        assert self._provider is not None
        retry_parts = [
            *parts,
            AiMessagePart(role="model", text=previous),
            AiMessagePart(role="user", text=instruction),
        ]
        result = await self._provider.complete(
            system=system,
            parts=retry_parts,
            tools=[],
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
        )
        return result.text or ""

    # ── Исходы ──────────────────────────────────────────────────────────────

    async def _deliver(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        reply: BotReply,
        verdict: Verdict,
        model_name: str,
    ) -> ReplyOutcome:
        await self._remember_lang(conversation, reply)
        auto = clinic.ai_mode is AiMode.AUTO
        sent_at: datetime | None = None
        wa_message_id: str | None = None
        if auto:
            binding = await self._whatsapp.binding_by_clinic(clinic.id)
            if binding is None or not binding.active:
                return await self._escalate(
                    conversation, clinic, "tool_error", {"note": "whatsapp not connected"}
                )
            try:
                sent = await self._wa.send_text(
                    binding.phone_number_id, conversation.wa_phone, reply.reply
                )
            except WhatsAppSendError as exc:
                return await self._escalate(
                    conversation, clinic, "tool_error", {"note": f"send: {exc}"}
                )
            sent_at = datetime.now(UTC)
            wa_message_id = sent.wa_message_id
            conversation.last_msg_at = sent_at

        message = await self._save_bot_message(
            conversation, reply, verdict, model_name, sent_at, wa_message_id
        )
        if auto:
            await publish_realtime(
                self._session,
                conversation.clinic_id,
                "conversation.message",
                {"conversationId": str(conversation.id), "direction": "out"},
            )
        await self._session.commit()
        return ReplyOutcome(
            status="sent" if auto else "shadow", message_id=message.id, reply=reply.reply
        )

    async def _blocked(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        reply: BotReply,
        verdict: Verdict,
        model_name: str,
    ) -> ReplyOutcome:
        """Валидатор заблокировал: ответ в журнал (sent_at=null) + эскалация."""
        message = await self._save_bot_message(
            conversation, reply, verdict, model_name, None, None
        )
        return await self._escalate(
            conversation,
            clinic,
            "validator_blocked",
            {"codes": verdict.codes, "notes": verdict.notes, "proposed": reply.reply},
            proposed_message_id=message.id,
        )

    async def _needs_human(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        reply: BotReply,
        verdict: Verdict,
        model_name: str,
    ) -> ReplyOutcome:
        message = await self._save_bot_message(
            conversation, reply, verdict, model_name, None, None
        )
        reason = "low_confidence" if "V06" in verdict.codes else "no_facts"
        return await self._escalate(
            conversation,
            clinic,
            reason,
            {"notes": verdict.notes, "proposed": reply.reply},
            proposed_message_id=message.id,
        )

    async def _escalate(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        reason: str,
        payload: dict[str, Any] | None,
        *,
        proposed_message_id: int | None = None,
        notify_patient: bool = True,
    ) -> ReplyOutcome:
        """Эскалация §8.2: карточка + событие + пауза бота + фраза пациенту.

        Шаблон пациенту уходит только в ``auto`` (в shadow пациент бота ещё не
        видел — писать ему от имени клиники нельзя) и моделью не переводится.
        ``notify_patient=False`` — когда пациенту уже написали своим шаблоном
        (лимит §14): два извинения подряд выглядят поломкой.
        """
        self._repository.add_escalation(
            conversation.id,
            conversation.clinic_id,
            reason=reason,
            payload={**(payload or {}), "proposedMessageId": proposed_message_id},
        )
        conversation.status = AiConversationStatus.ESCALATED
        await emit_event(
            self._session,
            clinic_id=conversation.clinic_id,
            type_="ai.escalated",
            entity_type="conversation",
            entity_id=conversation.id,
            payload={"reason": reason},
        )
        if notify_patient and clinic.ai_mode is AiMode.AUTO:
            binding = await self._whatsapp.binding_by_clinic(clinic.id)
            if binding is not None and binding.active:
                lang = "ky" if conversation.lang is Lang.KG else "ru"
                try:
                    sent = await self._wa.send_text(
                        binding.phone_number_id,
                        conversation.wa_phone,
                        _ESCALATION_TEXTS[lang],
                    )
                    now = datetime.now(UTC)
                    await self._whatsapp.add_message(
                        conversation,
                        role=AiMessageRole.SYSTEM,
                        body=_ESCALATION_TEXTS[lang],
                        wa_message_id=sent.wa_message_id,
                        sent_at=now,
                    )
                    conversation.last_msg_at = now
                except WhatsAppSendError as exc:
                    logger.warning("ai_escalation_notice_failed", error=str(exc))
        await self._session.commit()
        return ReplyOutcome(status="escalated", escalation_reason=reason)

    async def _turn_facts(
        self, clinic: Clinic, system: str, parts: list[AiMessagePart]
    ) -> TurnFacts:
        """Собрать факты хода для V01–V03 (§7.1).

        Источники — всё, что модель видела: системный промпт (карточка клиники,
        база знаний, глоссарий), история диалога и результаты инструментов
        этого хода. История нужна, чтобы бот мог повторить цену, названную
        проверенным ответом раньше, не получив блок за «выдумку».
        """
        sources: list[str] = [system]
        for part in parts:
            if part.text:
                sources.append(part.text)
            if part.tool_result is not None:
                sources.append(json.dumps(part.tool_result, ensure_ascii=False))
        doctors = (
            await self._session.execute(
                select(Account.full_name)
                .join(Membership, Membership.account_id == Account.id)
                .where(
                    Membership.clinic_id == clinic.id,
                    Membership.active.is_(True),
                    Membership.specialization_id.is_not(None),
                )
            )
        ).scalars()
        return TurnFacts.build(sources, [name for name in doctors if name])

    # ── Лимит потока (§14) ──────────────────────────────────────────────────

    async def _rate_limited(self, conversation: AiConversation) -> bool:
        """Пациент прислал больше нормы сообщений за последний час."""
        since = datetime.now(UTC) - timedelta(hours=1)
        count = (
            await self._session.execute(
                select(func.count())
                .select_from(AiMessage)
                .where(
                    AiMessage.conversation_id == conversation.id,
                    AiMessage.role == AiMessageRole.PATIENT,
                    AiMessage.created_at >= since,
                )
            )
        ).scalar_one()
        return int(count) > settings.ai_rate_limit_per_hour

    async def _too_many(
        self, conversation: AiConversation, clinic: Clinic
    ) -> ReplyOutcome:
        """Одно извинение и передача человеку — дальше бот молчит."""
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        await self._send_filter_reply(
            conversation, clinic, service_text("rate_limited", lang)
        )
        return await self._escalate(
            conversation, clinic, "rate_limited", None, notify_patient=False
        )

    # ── Медицинский фильтр (§9) ─────────────────────────────────────────────

    async def _last_patient_text(self, conversation: AiConversation) -> str:
        """Текст последнего сообщения пациента — вход медицинского фильтра."""
        return (
            await self._session.execute(
                select(AiMessage.body)
                .where(
                    AiMessage.conversation_id == conversation.id,
                    AiMessage.role == AiMessageRole.PATIENT,
                )
                .order_by(AiMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none() or ""

    async def _send_filter_reply(
        self, conversation: AiConversation, clinic: Clinic, body: str
    ) -> bool:
        """Отправить шаблон фильтра пациенту и записать его в диалог.

        Уходит только в ``auto``: в теневом режиме пациент бота ещё не видел,
        писать ему от имени клиники нельзя. Возвращает, ушло ли сообщение.
        """
        sent_at: datetime | None = None
        wa_message_id: str | None = None
        if clinic.ai_mode is AiMode.AUTO:
            binding = await self._whatsapp.binding_by_clinic(clinic.id)
            if binding is not None and binding.active:
                try:
                    sent = await self._wa.send_text(
                        binding.phone_number_id, conversation.wa_phone, body
                    )
                    sent_at = datetime.now(UTC)
                    wa_message_id = sent.wa_message_id
                    conversation.last_msg_at = sent_at
                except WhatsAppSendError as exc:
                    logger.warning("ai_medical_reply_failed", error=str(exc))
        message = await self._whatsapp.add_message(
            conversation,
            role=AiMessageRole.BOT,
            body=body,
            wa_message_id=wa_message_id,
            sent_at=sent_at,
        )
        # Метка в журнале: этот ответ написал фильтр, а не модель.
        message.validator = {"passed": True, "codes": [], "notes": "medical_filter"}
        return sent_at is not None

    async def _urgent(
        self, conversation: AiConversation, clinic: Clinic, medical: MedicalVerdict
    ) -> ReplyOutcome:
        """§9.2: телефон клиники, срочное уведомление, никакого диалога."""
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        phone = (clinic.business_phone or "").strip()
        body = (
            medical_text("urgent", lang).format(phone=phone)
            if phone
            else medical_text("urgent_no_phone", lang)
        )
        await self._send_filter_reply(conversation, clinic, body)
        # Бот замолкает: уточняющих вопросов при неотложном не задают.
        self._repository.add_escalation(
            conversation.id,
            conversation.clinic_id,
            reason="urgent_medical",
            payload={"trigger": medical.label, "proposedMessageId": None},
        )
        conversation.status = AiConversationStatus.ESCALATED
        await emit_event(
            self._session,
            clinic_id=conversation.clinic_id,
            type_="ai.urgent",
            entity_type="conversation",
            entity_id=conversation.id,
            payload={"trigger": medical.label},
        )
        await self._session.commit()
        logger.info(
            "ai_medical_urgent",
            conversation_id=str(conversation.id),
            trigger=medical.label,
        )
        return ReplyOutcome(status="escalated", escalation_reason="urgent_medical")

    async def _complaint(
        self, conversation: AiConversation, clinic: Clinic, medical: MedicalVerdict
    ) -> ReplyOutcome:
        """§9.1: состояние не обсуждаем — зовём к врачу и сразу даём слоты."""
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        slots = await self._nearest_slots(conversation.clinic_id)
        body = (
            medical_text("complaint", lang).format(slots=slots)
            if slots
            else medical_text("complaint_no_slots", lang)
        )
        auto = await self._send_filter_reply(conversation, clinic, body)
        await self._session.commit()
        logger.info(
            "ai_medical_complaint",
            conversation_id=str(conversation.id),
            trigger=medical.label,
        )
        return ReplyOutcome(status="sent" if auto else "shadow", reply=body)

    async def _nearest_slots(self, clinic_id: uuid.UUID) -> str:
        """Строка «03.09 в 10:00, 04.09 в 09:30» — ближайшие свободные дни."""
        days = await BookingService(self._session).free_days(
            clinic_id,
            date_from=datetime.now(UTC),
            days=3,
            doctor_membership_id=None,
            service_id=None,
        )
        parts = []
        for day in days[:2]:
            iso = str(day["date"])
            parts.append(f"{iso[8:10]}.{iso[5:7]} в {day['firstSlot']}")
        return ", ".join(parts)

    async def _save_bot_message(
        self,
        conversation: AiConversation,
        reply: BotReply,
        verdict: Verdict,
        model_name: str,
        sent_at: datetime | None,
        wa_message_id: str | None,
    ) -> AiMessage:
        message = AiMessage(
            conversation_id=conversation.id,
            clinic_id=conversation.clinic_id,
            role=AiMessageRole.BOT,
            body=reply.reply,
            wa_message_id=wa_message_id,
            used_facts=reply.used_facts,
            validator={
                "passed": verdict.action is VerdictAction.PASS,
                "codes": verdict.codes,
                "notes": verdict.notes,
            },
            model=model_name,
            sent_at=sent_at,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def _remember_lang(self, conversation: AiConversation, reply: BotReply) -> None:
        """Первый ответ фиксирует язык диалога и профиля (§10.2)."""
        if await self._repository.bot_replies_count(conversation.id) > 0:
            return
        lang = _SPEC_TO_LANG.get(reply.lang)
        if lang is None:
            return
        conversation.lang = lang
        if conversation.patient_id is not None:
            patient = await self._session.get(Patient, conversation.patient_id)
            if patient is not None and patient.preferred_lang is None:
                patient.preferred_lang = lang
