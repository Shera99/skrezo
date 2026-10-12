"""Оркестратор ответа ассистента: контекст → модель ⇄ инструменты → валидатор → режим.

Поток §2.1: расшифровка голосового (ASR) → медфильтр → модель ⇄ инструменты →
валидатор → режим. Дисциплина ошибок — §14: один повтор при сбое модели, затем
эскалация; недоступный провайдер — полная эскалация, бот молчит. Тишина лучше
выдумки.

Режимы (§13.1): ``shadow`` — ответ сохраняется с ``sent_at = null`` («я бы
ответил так»), пациенту не уходит; ``assisted`` — ответ становится черновиком
``review = pending`` на решение сотрудника (Ф5, ``_draft``); ``auto`` — уходит
всё, что прошло валидатор. Пока диалог ``escalated`` — бот молчит.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ai.application.booking import BookingService
from app.modules.ai.application.knowledge_gaps import KnowledgeGapService
from app.modules.ai.application.regression_cases import RegressionCaseService
from app.modules.ai.application.summary import recent_summaries
from app.modules.ai.application.tools import ToolContext, ToolExecutor, tool_decls_for
from app.modules.ai.domain.facts import TurnFacts
from app.modules.ai.domain.language import detect_lang
from app.modules.ai.domain.medical import MedicalKind, MedicalVerdict, classify
from app.modules.ai.domain.prompt import (
    ClinicCard,
    build_system_prompt,
    clock_line,
    without_clock,
)
from app.modules.ai.domain.reply import BotReply, ReplyParseError, parse_reply
from app.modules.ai.domain.skills import effective_skills
from app.modules.ai.domain.texts import medical_text, service_text, slots_phrase
from app.modules.ai.domain.validator import Verdict, VerdictAction, validate
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.ai.infrastructure.repositories.knowledge_gap_repository import (
    KnowledgeGapRepository,
)
from app.modules.push.application.outbox import emit_event
from app.modules.usage.application.services.usage_meter import UsageMeter
from app.modules.whatsapp.application.services.credentials import client_for
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.ai import AiMessagePart, AiProvider, AiProviderError, AiProviderUnavailableError
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

#: Сколько чужих клиник тянем для V09 — проверка подстрокой, список короткий.
_FOREIGN_LIMIT = 500

#: Шаблон «уточню у администратора» — двуязычный, моделью НЕ переводится (§10.2).
_ESCALATION_TEXTS: dict[str, str] = {
    "ru": "Уточню у администратора и вернусь с ответом.",
    "ky": "Администратордон тактап, жооп берем.",
}

#: Язык спеки (ru|ky) → наш Lang для conversation/patient.
_SPEC_TO_LANG = {"ru": Lang.RU, "ky": Lang.KG}

#: Блок валидатора, который модель может исправить сама: в контексте хода уже
#: есть верные цены, время и врачи — она просто взяла не то или не сослалась.
#: Один повтор с подсказкой вместо мгновенного «уточню у администратора».
_FIXABLE_CODES = frozenset({"V01", "V02", "V03", "V05"})
_FIX_HINTS: dict[str, str] = {
    "V01": (
        "В ответе цена, которой нет в результатах инструментов ({notes}). Называй цены "
        "только из get_services этого хода или не называй их."
    ),
    "V02": (
        "В ответе время или дата не из слотов ({notes}). Называй только время из поля "
        "time результатов get_available_slots этого хода; подходящего нет — так и скажи."
    ),
    "V03": (
        "В ответе врач, которого нет в справочнике клиники ({notes}). Называй только "
        "врачей из get_doctors или не называй врача."
    ),
    "V05": (
        "В ответе есть цена или время, но used_facts пуст. Перечисли в used_facts "
        "идентификаторы фактов, на которых основан ответ, или убери числа из ответа."
    ),
}


class _UnparseableReplyError(Exception):
    """Ни одна попытка хода не дала разбираемого JSON; ``raw`` — текст последней.

    Если первая попытка разобралась, а мусор прислал повтор, этого исключения
    нет — остаётся её вердикт. 12.10 было наоборот: верный ответ «Здравствуйте!
    …» заблокировал ложный V03, повтор не разобрался, и в журнал как
    «непарсибельный» ушёл текст первой попытки — настоящая причина пропала.
    """

    def __init__(self, raw: str) -> None:
        super().__init__("непарсибельный ответ")
        self.raw = raw


def _lang_code(lang: Lang) -> str:
    """Наш Lang → язык спеки модели (ru|ky)."""
    return "ky" if lang is Lang.KG else "ru"


def _clinic_zone(clinic: Clinic) -> ZoneInfo:
    """Часовой пояс клиники: «сейчас» и «сегодня» пациенту — по её часам."""
    try:
        return ZoneInfo(clinic.timezone or "Asia/Bishkek")
    except KeyError, ValueError:
        return ZoneInfo("Asia/Bishkek")


def with_clock(
    parts: list[AiMessagePart], clinic: Clinic, now: datetime | None = None
) -> list[AiMessagePart]:
    """Строка «СЕЙЧАС» в начало последней реплики пациента.

    Не в системный промпт: тот кэшируется у провайдера на клинику, и минутная
    точность сбрасывала бы кэш на каждом ходе. Последняя реплика — потому что
    именно её модель читает как «текущий момент».
    """
    moment = (now or datetime.now(UTC)).astimezone(_clinic_zone(clinic))
    stamped = list(parts)
    last = stamped[-1]
    stamped[-1] = AiMessagePart(
        role=last.role, text=f"{clock_line(moment)}\n\n{last.text or ''}".rstrip()
    )
    return stamped


def _fixable(verdict: Verdict) -> bool:
    return (
        verdict.action is VerdictAction.BLOCK
        and bool(verdict.codes)
        and set(verdict.codes) <= _FIXABLE_CODES
    )


def _fix_instruction(verdict: Verdict) -> str:
    """Подсказка модели для повтора: что именно не сошлось с фактами хода."""
    hints = [_FIX_HINTS[code].format(notes=verdict.notes or "—") for code in verdict.codes]
    return " ".join([*hints, "Верни строго один JSON."])


@dataclass(frozen=True, slots=True)
class ReplyOutcome:
    """Что произошло с ходом ассистента (для симулятора и тестов)."""

    status: str  # sent | shadow | escalated | skipped | deferred
    message_id: int | None = None
    reply: str | None = None
    escalation_reason: str | None = None
    #: Пояснение к skipped/deferred: «already_answered», причина недоступности.
    note: str | None = None


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
        self._meter = UsageMeter(session)
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
        last = await self._last_patient_message(conversation)
        if last is None:
            return ReplyOutcome(status="skipped", note="нет сообщений пациента")
        # Ф8: после последнего хода пациента уже есть наш — отвечать нечего.
        # Так повторное или отложенное задание не даёт второго ответа.
        if await self._repository.answered_after(conversation.id, last.id):
            return ReplyOutcome(status="skipped", note="already_answered")

        # §10.2: язык ответа — язык последнего сообщения пациента. Переключаем
        # диалог сразу, до фильтров: шаблоны медфильтра и «уточню у
        # администратора» тоже должны уйти на языке, которым он сейчас пишет.
        detected = detect_lang(last.transcript or last.body)
        await self._follow_lang(conversation, detected)

        # §11: сообщение, которое мы не прочитали (голосовое без надёжной
        # расшифровки, снимок, документ), — к человеку. Отвечать на него
        # «чем помочь?» хуже, чем честно передать администратору.
        unread = _unreadable_reason(last)
        if unread is not None:
            return await self._escalate(conversation, clinic, unread, None)

        # §9: медицинский фильтр — детерминированно и ДО модели. Стоит выше
        # проверки провайдера намеренно: неотложное состояние пациента не
        # должно зависеть от того, доступна ли сейчас модель.
        medical = classify(last.transcript or last.body)
        if medical.kind is not MedicalKind.NONE:
            # Ф6: сработавший фильтр — кейс регрессии на то, что сработает и завтра.
            await RegressionCaseService(self._session).from_medical(
                conversation, question=last.transcript or last.body, kind=medical.kind.value
            )
        if medical.kind is MedicalKind.URGENT:
            return await self._urgent(conversation, clinic, medical)
        if medical.kind is MedicalKind.ACUTE_PAIN:
            return await self._acute_pain(conversation, clinic, medical)
        if medical.kind is MedicalKind.COMPLAINT:
            return await self._complaint(conversation, clinic, medical)

        # §14: поток сообщений от одного пациента. Молча игнорировать нельзя —
        # человек решит, что его не слышат; отвечаем один раз и зовём человека.
        if await self._rate_limited(conversation):
            return await self._too_many(conversation, clinic)

        if self._provider is None:
            logger.warning("ai_provider_missing", clinic_id=str(clinic.id))
            return ReplyOutcome(status="skipped")

        try:
            return await self._converse(
                conversation, clinic, self._provider, detected_lang=detected
            )
        except AiProviderUnavailableError as exc:
            # Временно недоступная модель (Ф8): ход откладывает и повторяет
            # координатор задания — эскалация здесь стала бы лавиной.
            logger.warning("ai_provider_unavailable", error=str(exc))
            await self._session.commit()
            return ReplyOutcome(status="deferred", note=str(exc))

    async def _converse(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        provider: AiProvider,
        *,
        detected_lang: str | None = None,
    ) -> ReplyOutcome:
        """Ход модели с инструментами → разбор → валидатор → доставка.

        :class:`AiProviderUnavailableError` отсюда пробрасывается: решение «отложить
        и повторить» принимает координатор задания, а не этот слой.
        """
        # §10.2: язык хода — язык последнего сообщения пациента (детектор уже
        # переключил на него диалог). Не распознан — язык диалога; а до первого
        # прошедшего ответа бота язык выбирает модель сама, и V07 не применяется.
        first_reply = await self._repository.bot_replies_count(conversation.id) == 0
        lang: str | None = (
            None if first_reply and detected_lang is None else _lang_code(conversation.lang)
        )
        skills = effective_skills(clinic.ai_mode, clinic.ai_skills)
        system = await self.system_prompt(clinic, lang)
        cache_scope = f"clinic:{clinic.id}"
        # Дата и время — в каждом ходе: без них модель брала год из памяти.
        parts = with_clock(await self._history_parts(conversation), clinic)
        executor = ToolExecutor(self._session, self._repository)
        binding = await self._whatsapp.binding_by_clinic(clinic.id)
        tool_context = ToolContext(
            conversation=conversation,
            clinic=clinic,
            wa_client=client_for(self._wa, binding) if binding is not None else self._wa,
            binding_phone_number_id=(
                binding.phone_number_id if binding is not None and binding.active else None
            ),
            skills=skills,
        )
        tools = tool_decls_for(skills)

        escalate_call: dict[str, Any] | None = None
        raw_text: str | None = None
        model_name = provider.model_name
        try:
            for _ in range(settings.ai_max_tool_calls + 1):
                # Ф8: отпустить соединение с БД на время вызова модели — секунды
                # ожидания не должны держать слот пула (иначе потолок ≈15
                # одновременных ответов на процесс). Записи инструментов
                # фиксируются здесь же: раньше их фиксировал конец хода, и все
                # выходы конвейера коммитят — семантика не меняется.
                await self._session.commit()
                result = await provider.complete(
                    system=system,
                    parts=parts,
                    tools=tools,
                    temperature=settings.ai_temperature,
                    max_tokens=settings.ai_max_tokens,
                    cache_scope=cache_scope,
                )
                await self._meter.model_call(clinic.id, result.usage)
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
                # Инструмент может потребовать человека сам (§6.5 «чужой
                # телефон», Ф7 «запись подтверждает администратор»): такие
                # случаи не отдаём на усмотрение модели.
                if isinstance(outcome, dict) and outcome.get("_escalate"):
                    escalate_call = {
                        "reason": str(outcome["_escalate"]),
                        "note": outcome.get("_note"),
                    }
                    break
                parts.append(
                    AiMessagePart(
                        role="model",
                        tool_name=call.name,
                        tool_args=call.args,
                        tool_signature=call.signature,
                    )
                )
                parts.append(AiMessagePart(role="tool", tool_name=call.name, tool_result=outcome))
            else:
                # Потолок вызовов — модель зациклилась на инструментах.
                return await self._escalate(
                    conversation, clinic, "tool_error", {"note": "tool loop limit"}
                )
        except AiProviderUnavailableError:
            raise
        except AiProviderError as exc:
            logger.warning("ai_provider_error", error=str(exc))
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": f"provider: {exc}"}
            )

        if escalate_call is not None:
            reason = str(escalate_call.get("reason") or "patient_asked")
            if reason == "unknown":
                # Модель сама сказала «в базе знаний нет» — пробел (Ф6).
                note = escalate_call.get("note")
                await self._note_gap(
                    conversation, source="model_unknown", hint=str(note) if note else None
                )
            return await self._escalate(
                conversation, clinic, reason, {"note": escalate_call.get("note")}
            )
        if raw_text is None:
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": "модель не дала текста"}
            )

        facts = await self.turn_facts(clinic, system, parts)
        try:
            reply, verdict = await self._parse_and_validate(
                conversation, system, parts, raw_text, lang, facts
            )
        except AiProviderUnavailableError:
            raise
        except AiProviderError as exc:
            # Перегенерация упёрлась в провайдера — раньше это роняло задание.
            logger.warning("ai_provider_error", error=str(exc))
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": f"provider: {exc}"}
            )
        except _UnparseableReplyError as exc:
            return await self._escalate(
                conversation,
                clinic,
                "validator_blocked",
                # Хвосты сырого текста — иначе «непарсибельный» нечем разбирать.
                {"note": "непарсибельный ответ", "raw": exc.raw[:300], "firstRaw": raw_text[:300]},
            )
        if verdict.action is VerdictAction.BLOCK:
            return await self._blocked(conversation, clinic, reply, verdict, model_name)
        if verdict.action is VerdictAction.ESCALATE:
            return await self._needs_human(conversation, clinic, reply, verdict, model_name)
        return await self._deliver(conversation, clinic, reply, verdict, model_name)

    async def give_up(self, conversation_id: uuid.UUID, note: str) -> ReplyOutcome:
        """Модель не ожила за отведённые повторы — передать человеку (§14).

        Зовёт координатор после ``ai_reply_max_defers`` отложенных попыток:
        пациент ждал достаточно, дальше тишина хуже честного «уточню».
        """
        conversation = await self._session.get(AiConversation, conversation_id)
        if conversation is None or conversation.status is not AiConversationStatus.ACTIVE:
            return ReplyOutcome(status="skipped")
        clinic = await self._session.get(Clinic, conversation.clinic_id)
        if clinic is None:
            return ReplyOutcome(status="skipped")
        return await self._escalate(conversation, clinic, "tool_error", {"note": note})

    # ── Контекст ────────────────────────────────────────────────────────────

    async def system_prompt(self, clinic: Clinic, lang: str | None) -> str:
        """Системный промпт клиники — одинаков для всех её диалогов на языке.

        Ф8: именно поэтому в нём нет ничего про пациента — префикс кэшируется
        у провайдера на клинику; резюме прошлых обращений едут в первую
        реплику (см. ``_history_parts``).
        """
        glossary = await self._repository.approved_glossary(clinic.id)
        kb = await self._repository.kb_for_prompt(clinic.id, lang or "ru")
        summaries: list[str] = []
        card = ClinicCard(
            name=clinic.name,
            address=clinic.address,
            phone=clinic.business_phone,
            working_hours=None,  # человекочитаемый график — вместе с KB-редактором
        )
        return build_system_prompt(
            card, lang, glossary, kb, summaries, effective_skills(clinic.ai_mode, clinic.ai_skills)
        )

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
                # Голосовое участвует в диалоге расшифровкой (§11).
                parts.append(AiMessagePart(role="user", text=message.transcript or message.body))
            elif (
                message.role in (AiMessageRole.BOT, AiMessageRole.ADMIN)
                and message.sent_at is not None
            ):
                # Отправленные ответы бота и человека — «наша сторона» диалога:
                # модель обязана видеть, что клиника уже сказала пациенту.
                parts.append(AiMessagePart(role="model", text=message.body))
        if not parts or parts[-1].role != "user":
            parts.append(AiMessagePart(role="user", text=""))
        # §4.1: последние пять резюме по номеру — разговор не с чистого листа.
        # Ф8: они про пациента, а не про клинику, поэтому не в системном промпте
        # (тот кэшируется на клинику), а в первой реплике пациента.
        summaries = await recent_summaries(
            self._session, conversation.clinic_id, conversation.wa_phone
        )
        if summaries:
            first = next(i for i, part in enumerate(parts) if part.role == "user")
            past = "\n".join(f"— {s}" for s in summaries)
            parts[first] = AiMessagePart(
                role="user",
                text=(
                    "О ЧЁМ ГОВОРИЛИ РАНЬШЕ (прошлые обращения этого номера, свежие "
                    f"сверху; фон, а не факты для ответа):\n{past}\n\n{parts[first].text or ''}"
                ),
            )
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
    ) -> tuple[BotReply, Verdict]:
        """Разобрать ответ; один повтор на исправимое (§7, §14), затем как есть.

        Повтор получают: битый JSON, язык и длина (V07/V08) и блок по фактам
        (V01–V03, V05) — модель видит верные цены и слоты в контексте хода и
        обычно просто взяла не то или не сослалась на факт. Раньше такой блок
        сразу уводил пациента к администратору фразой «уточню и вернусь».
        Запреты (V04) и чужая клиника (V09) не повторяются — это не опечатка.

        Повтор прислал не JSON — остаётся вердикт первой попытки: в журнале
        должна быть настоящая причина (код блока и предложенный текст), а не
        «непарсибельный ответ». :class:`_UnparseableReplyError` — только когда не
        разобралась ни одна попытка.
        """
        retried: list[str] = []
        first: tuple[BotReply, Verdict] | None = None
        for attempt in range(2):
            try:
                reply = parse_reply(raw_text)
            except ReplyParseError as exc:
                if attempt == 1:
                    if first is None:
                        raise _UnparseableReplyError(raw_text) from exc
                    first_reply, first_verdict = first
                    if first_verdict.action is VerdictAction.REGENERATE:
                        # Повтор не помог — по §7 дальше эскалация.
                        first_verdict = Verdict(
                            VerdictAction.ESCALATE, first_verdict.codes, first_verdict.notes
                        )
                    return first_reply, first_verdict
                raw_text = await self._retry(
                    conversation.clinic_id,
                    system,
                    parts,
                    raw_text,
                    f"Ответ не JSON ({exc}). Верни строго один JSON.",
                )
                continue
            verdict = validate(reply, lang, facts)
            retryable = verdict.action is VerdictAction.REGENERATE or _fixable(verdict)
            if not retryable or attempt == 1:
                if verdict.action is VerdictAction.REGENERATE:
                    # Повтор не помог — по §7 дальше эскалация.
                    verdict = Verdict(VerdictAction.ESCALATE, verdict.codes, verdict.notes)
                if retried and verdict.action is VerdictAction.PASS:
                    # Метка в журнале: ответ прошёл со второй попытки.
                    verdict = Verdict(
                        VerdictAction.PASS, notes=f"после повтора: {', '.join(retried)}"
                    )
                if retried:
                    # Коды первой попытки — при ЛЮБОМ исходе повтора: выдумку,
                    # после которой модель сказала «не знаю», лестница доверия
                    # тоже обязана посчитать фактической ошибкой.
                    verdict = replace(verdict, retried_codes=retried)
                return reply, verdict
            retried = list(verdict.codes)
            first = (reply, verdict)
            logger.info(
                "ai_reply_regenerate",
                conversation_id=str(conversation.id),
                codes=verdict.codes,
                notes=verdict.notes,
            )
            if verdict.action is VerdictAction.REGENERATE:
                instruction = (
                    f"Ответь на языке {lang or 'ru'} и не длиннее 1000 символов. "
                    "Верни строго один JSON."
                )
            else:
                instruction = _fix_instruction(verdict)
            raw_text = await self._retry(
                conversation.clinic_id, system, parts, raw_text, instruction
            )
        raise _UnparseableReplyError(raw_text)  # недостижимо: вторая попытка всегда возвращает

    async def _retry(
        self,
        clinic_id: uuid.UUID,
        system: str,
        parts: list[AiMessagePart],
        previous: str,
        instruction: str,
    ) -> str:
        if self._provider is None:
            return ""
        retry_parts = [
            *parts,
            AiMessagePart(role="model", text=previous),
            AiMessagePart(role="user", text=instruction),
        ]
        # Ф8: соединение с БД отпускаем и на перегенерацию — см. _converse.
        await self._session.commit()
        result = await self._provider.complete(
            system=system,
            parts=retry_parts,
            tools=[],
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            cache_scope=f"clinic:{clinic_id}",
        )
        await self._meter.model_call(clinic_id, result.usage)
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
        if clinic.ai_mode is AiMode.ASSISTED:
            return await self._draft(conversation, clinic, reply, verdict, model_name)
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
                sent = await client_for(self._wa, binding).send_text(
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
            await self._signal_message(conversation)
        await self._session.commit()
        return ReplyOutcome(
            status="sent" if auto else "shadow", message_id=message.id, reply=reply.reply
        )

    async def _draft(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        reply: BotReply,
        verdict: Verdict,
        model_name: str,
    ) -> ReplyOutcome:
        """Режим «Проверяю» (Ф5): ответ становится черновиком на решение человека.

        Пациенту ничего не уходит. Прежний черновик диалога, если его не успели
        рассмотреть, помечается ``superseded``: решение принимают по свежему —
        он видит все сообщения пациента. Администратора зовёт событие
        ``ai.draft_ready`` (обычный приоритет: тихие часы соблюдаются).
        """
        await self._repository.supersede_pending_drafts(conversation.id)
        message = await self._save_bot_message(conversation, reply, verdict, model_name, None, None)
        message.review = "pending"
        await emit_event(
            self._session,
            clinic_id=conversation.clinic_id,
            type_="ai.draft_ready",
            entity_type="conversation",
            entity_id=conversation.id,
            payload={"messageId": message.id},
        )
        await self._signal_message(conversation, "draft")
        await self._session.commit()
        return ReplyOutcome(status="draft", message_id=message.id, reply=reply.reply)

    async def _signal_message(self, conversation: AiConversation, direction: str = "out") -> None:
        """Сигнал открытым экранам клиники (Ф4): в диалоге новое сообщение.

        Публикуется в текущей транзакции и уходит вместе с её ``commit()``.
        """
        await publish_realtime(
            self._session,
            conversation.clinic_id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": direction},
        )

    async def _signal_status(self, conversation: AiConversation) -> None:
        """Сигнал открытым экранам клиники (Ф4): статус диалога сменился.

        Без него открытый чат показывал «ведёт бот», хотя диалог уже передан
        администратору, — до ручного обновления экрана. ``inbox.new`` от
        события ``ai.escalated`` приходит позже и про сам диалог не говорит.
        """
        await publish_realtime(
            self._session,
            conversation.clinic_id,
            "conversation.status",
            {"conversationId": str(conversation.id), "status": conversation.status.value},
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
        message = await self._save_bot_message(conversation, reply, verdict, model_name, None, None)
        if not reply.used_facts:
            # Ответ без единого факта = модели не хватило знаний — в очередь (Ф6).
            # Ошибка при наличии фактов (перепутала цену) — не пробел, а промах.
            await self._note_gap(conversation, source="no_facts", hint=verdict.notes)
        last = await self._last_patient_message(conversation)
        question = ((last.transcript or last.body) if last else "").strip()
        if question:
            # Ф6: снятая выдумка — кейс регрессии «на этот вопрос не выдумывать».
            await RegressionCaseService(self._session).from_incident(
                conversation, question=question, proposed=reply.reply, codes=list(verdict.codes)
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
        message = await self._save_bot_message(conversation, reply, verdict, model_name, None, None)
        reason = "low_confidence" if "V06" in verdict.codes else "no_facts"
        # Ф6: честное «не знаю» — пробел знаний, а не просто эскалация.
        await self._note_gap(conversation, source=reason, hint=reply.human_reason or verdict.notes)
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
        await self._signal_status(conversation)
        if notify_patient and clinic.ai_mode is AiMode.AUTO:
            binding = await self._whatsapp.binding_by_clinic(clinic.id)
            if binding is not None and binding.active:
                lang = "ky" if conversation.lang is Lang.KG else "ru"
                try:
                    sent = await client_for(self._wa, binding).send_text(
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
                    await self._signal_message(conversation)
                except WhatsAppSendError as exc:
                    logger.warning("ai_escalation_notice_failed", error=str(exc))
        await self._session.commit()
        return ReplyOutcome(status="escalated", escalation_reason=reason)

    async def turn_facts(
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
                # Строка «СЕЙЧАС» — подсказка модели, а не факт для ответа.
                sources.append(without_clock(part.text))
            if part.tool_result is not None:
                sources.append(json.dumps(part.tool_result, ensure_ascii=False))
        # V09: названия остальных клиник платформы — их упоминание в ответе
        # означает либо утечку контекста, либо выдумку.
        foreign = (
            await self._session.execute(
                select(Clinic.name).where(Clinic.id != clinic.id).limit(_FOREIGN_LIMIT)
            )
        ).scalars()
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
        return TurnFacts.build(
            sources,
            [name for name in doctors if name],
            [name for name in foreign if name],
            own_clinic=clinic.name,
        )

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

    async def _too_many(self, conversation: AiConversation, clinic: Clinic) -> ReplyOutcome:
        """Одно извинение и передача человеку — дальше бот молчит."""
        lang = "ky" if conversation.lang is Lang.KG else "ru"
        await self._send_filter_reply(conversation, clinic, service_text("rate_limited", lang))
        return await self._escalate(
            conversation, clinic, "rate_limited", None, notify_patient=False
        )

    # ── Медицинский фильтр (§9) ─────────────────────────────────────────────

    async def _last_patient_message(self, conversation: AiConversation) -> AiMessage | None:
        """Последний ход пациента: он же вход фильтра и проверки расшифровки."""
        return (
            await self._session.execute(
                select(AiMessage)
                .where(
                    AiMessage.conversation_id == conversation.id,
                    AiMessage.role == AiMessageRole.PATIENT,
                )
                .order_by(AiMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _note_gap(
        self, conversation: AiConversation, *, source: str, hint: str | None
    ) -> None:
        """Пробел знаний (Ф6): бот не ответил на вопрос — в очередь «Обучение».

        Пишется в той же транзакции, что и эскалация, которую объясняет:
        коммит делает вызывающий.
        """
        last = await self._last_patient_message(conversation)
        question = ((last.transcript or last.body) if last else "").strip()
        if not question:
            return
        await KnowledgeGapService(self._session, KnowledgeGapRepository(self._session)).record(
            clinic_id=conversation.clinic_id,
            conversation_id=conversation.id,
            question=question,
            lang="ky" if conversation.lang is Lang.KG else "ru",
            source=source,
            hint=hint,
        )

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
                    sent = await client_for(self._wa, binding).send_text(
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
        if sent_at is not None:
            # Как и ответ модели: на экран — только то, что ушло пациенту.
            await self._signal_message(conversation)
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
        await self._signal_status(conversation)
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
        lang = _lang_code(conversation.lang)
        slots = await self._nearest_slots(clinic, lang)
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

    async def _nearest_slots(self, clinic: Clinic, lang: str) -> str:
        """«сегодня в 15:00, 15:30, 16:00» — ближайшее свободное время по всем врачам.

        Пусто — свободного времени в ближайшие дни нет. Раньше здесь искались
        дни без врача и услуги, правило подбора слотов отвечало отказом, и шаблон
        жалобы ни разу не показал пациенту время.
        """
        starts = await BookingService(self._session).earliest_slots(clinic.id)
        today = datetime.now(_clinic_zone(clinic)).date()
        return slots_phrase(starts, lang, today=today)

    async def _acute_pain(
        self, conversation: AiConversation, clinic: Clinic, medical: MedicalVerdict
    ) -> ReplyOutcome:
        """Сильная боль без признаков неотложного: ближайшее время + администратор.

        Решение владельца 12.10.2026: для стоматологии это самая частая причина
        записи, поэтому бот не замолкает и не уводит пациента к человеку.
        Пациенту — шаблон с ближайшим временем (сегодня, если оно есть), а
        администратору — уведомление ``ai.urgent_booking``: он может позвонить
        сам. Диалог остаётся у бота — «давайте на 15:00» он ведёт к записи.
        Не ушло или нет свободного времени — передача человеку (см. ниже).
        """
        lang = _lang_code(conversation.lang)
        slots = await self._nearest_slots(clinic, lang)
        body = (
            medical_text("acute_pain", lang).format(slots=slots)
            if slots
            else medical_text("acute_pain_no_slots", lang)
        )
        sent = await self._send_filter_reply(conversation, clinic, body)
        logger.info(
            "ai_medical_acute_pain",
            conversation_id=str(conversation.id),
            trigger=medical.label,
            has_slots=bool(slots),
            sent=sent,
        )
        if sent and slots:
            await emit_event(
                self._session,
                clinic_id=conversation.clinic_id,
                type_="ai.urgent_booking",
                entity_type="conversation",
                entity_id=conversation.id,
                payload={"trigger": medical.label, "slots": slots},
            )
            await self._session.commit()
            return ReplyOutcome(status="sent", reply=body)
        # Время пациенту не ушло (теневой режим, сбой отправки) или свободного
        # нет — «бот предложил время» было бы неправдой, а ждать обычного пуша
        # пациенту с сильной болью нельзя: передача человеку с critical-событием.
        # Причина «липкая» — автовозврат боту такой диалог не заберёт.
        return await self._escalate(
            conversation,
            clinic,
            "acute_pain",
            {"trigger": medical.label, "slots": slots or None},
            notify_patient=False,
        )

    async def _follow_lang(self, conversation: AiConversation, detected: str | None) -> None:
        """Переключить язык диалога на язык последнего сообщения пациента (§10.2).

        ``None`` (имя, число, «ок») язык не меняет: одна реплика без признаков
        не должна переводить разговор. Профиль пациента заполняется, только
        если язык в нём ещё не задан: пациент глобальный, и одно сообщение в
        одной клинике не должно менять язык напоминаний во всех остальных.
        """
        lang = _SPEC_TO_LANG.get(detected or "")
        if lang is None or conversation.lang is lang:
            return
        conversation.lang = lang
        if conversation.patient_id is not None:
            patient = await self._session.get(Patient, conversation.patient_id)
            if patient is not None and patient.preferred_lang is None:
                patient.preferred_lang = lang

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
                **({"retriedCodes": verdict.retried_codes} if verdict.retried_codes else {}),
            },
            # Ф5: уверенность — метка кабинета «уверенный ответ» / «проверьте текст».
            confidence=reply.confidence,
            model=model_name,
            sent_at=sent_at,
        )
        self._session.add(message)
        await self._session.flush()
        # Учёт: ответ ассистента и диалог (последний — один раз за его жизнь).
        await self._meter.assistant_reply(conversation)
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


def _unreadable_reason(message: AiMessage | None) -> str | None:
    """Причина эскалации, если последний ход пациента мы прочитать не смогли.

    Ошибка расшифровки на входе даёт уверенно неправильный ответ на выходе —
    худший из возможных сценариев (§11), поэтому порог уверенности жёсткий,
    а снимки и документы в первой версии не интерпретируются вовсе.
    """
    if message is None or not message.media_type:
        return None
    if message.media_type != "audio":
        return "unsupported_media"  # снимок/документ: сохраняем, зовём человека
    if not (message.transcript or "").strip():
        return "asr_unavailable"
    if (message.transcript_confidence or 0.0) < settings.speech_min_confidence:
        return "asr_low_confidence"
    return None
