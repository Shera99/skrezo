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
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ai.application.booking import CONFIRM_PREFIX, BookingService, UpcomingVisit
from app.modules.ai.application.knowledge_gaps import KnowledgeGapService
from app.modules.ai.application.regression_cases import RegressionCaseService
from app.modules.ai.application.summary import recent_summaries
from app.modules.ai.application.tools import (
    BUTTON_TOOLS,
    ToolContext,
    ToolExecutor,
    button_title,
    tool_decls_for,
)
from app.modules.ai.domain.facts import TurnFacts
from app.modules.ai.domain.handoff import next_opening
from app.modules.ai.domain.language import LangGuess, guess_lang
from app.modules.ai.domain.medical import MedicalKind, MedicalVerdict, classify
from app.modules.ai.domain.prompt import (
    ClinicCard,
    build_system_prompt,
    clock_line,
    without_clock,
)
from app.modules.ai.domain.reply import BotReply, ReplyParseError, parse_reply
from app.modules.ai.domain.skills import effective_skills
from app.modules.ai.domain.texts import (
    is_handoff_notice,
    medical_text,
    opening_phrase,
    service_text,
    slots_phrase,
    visit_phrase,
)
from app.modules.ai.domain.validator import Verdict, VerdictAction, validate
from app.modules.ai.infrastructure.repositories.ai_repository import (
    STICKY_ESCALATIONS,
    AiRepository,
)
from app.modules.ai.infrastructure.repositories.knowledge_gap_repository import (
    KnowledgeGapRepository,
)
from app.modules.push.application.outbox import emit_event
from app.modules.schedule.domain.availability import default_working_hours
from app.modules.schedule.domain.models import WorkingHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import (
    ScheduleRepository,
)
from app.modules.usage.application.services.usage_meter import UsageMeter
from app.modules.whatsapp.application.services.credentials import client_for
from app.modules.whatsapp.domain.delivery import DeliveryFailure, describe_failure
from app.modules.whatsapp.domain.window import WINDOW
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
from app.shared.db.models.slot_hold import SlotHold
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

logger = structlog.get_logger(__name__)

#: Коды «формы» ответа (язык V07, длина V08): не исправились повтором — это
#: не просьба о человеке, и при ушедшей кнопке хватает подсказки нажать её.
_FORMAT_CODES = frozenset({"V07", "V08"})

#: Сколько чужих клиник тянем для сигнала V09 (поиск целыми словами).
_FOREIGN_LIMIT = 500

#: Язык спеки (ru|ky) → наш Lang для conversation/patient.
_SPEC_TO_LANG = {"ru": Lang.RU, "ky": Lang.KG}

#: Передачи, которые заводятся, даже если сотрудник только что вернул диалог
#: боту: здоровье пациента, поток сообщений, перехват и технические сбои —
#: «администратор уже в курсе» их не заменит.
_ALWAYS_HAND_OFF = frozenset({*STICKY_ESCALATIONS, "tool_error"})

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


def _speaks_to_patient(clinic: Clinic) -> bool:
    """Пишет ли ассистент пациенту служебные фразы от имени клиники.

    В ``shadow`` пациент бота ещё не видел — нельзя. В ``assisted`` и ``auto``
    можно: в «Проверяю» через администратора идут ответы модели, а шаблоны
    передачи и медфильтра — текст клиники. Инцидент 22–31.10.2026: клиника в
    ``assisted``, четыре передачи администратору — и ни слова пациенту.
    """
    return clinic.ai_mode in (AiMode.ASSISTED, AiMode.AUTO)


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
class _TemplateSend:
    """Ушёл ли шаблон пациенту и, если нет, почему WhatsApp его не принял."""

    sent: bool
    failure: DeliveryFailure | None = None


@dataclass(frozen=True, slots=True)
class _SentButton:
    """Кнопка подтверждения, ушедшая пациенту в этом ходу."""

    tool: str
    #: ``hold:<uuid>`` резерва — по нему видно, нажал ли пациент кнопку.
    confirmation_id: str | None


@dataclass(frozen=True, slots=True)
class ReplyOutcome:
    """Что произошло с ходом ассистента (для симулятора и тестов)."""

    #: held — сотрудник уже закрыл передачу этого сообщения, вторую не заводим.
    status: str  # sent | shadow | draft | escalated | held | skipped | deferred | button
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
        #: Время последнего сообщения пациента в этом ходе: по нему
        #: :meth:`_escalate` узнаёт, не закрыл ли сотрудник уже передачу этого
        #: самого сообщения («вернуть боту»).
        self._last_patient_at: datetime | None = None
        #: Язык этого хода выбирает модель (детектор не уверен или это первый
        #: ответ без признаков языка): V07 не применяется, выбор запоминается.
        self._model_picks_lang = False

    async def execute(self, conversation_id: uuid.UUID) -> ReplyOutcome:
        """Ответить на последнее сообщение пациента в диалоге (если положено)."""
        conversation = await self._session.get(AiConversation, conversation_id)
        if conversation is None:
            return ReplyOutcome(status="skipped")
        clinic = await self._session.get(Clinic, conversation.clinic_id)
        if clinic is None or clinic.ai_mode is AiMode.OFF:
            return ReplyOutcome(status="skipped")
        if conversation.status is AiConversationStatus.ESCALATED:
            # Человек ведёт разговор, бот молчит до возврата (§8.4) — кроме
            # неотложного: его шаблон не ждёт ни администратора, ни возврата.
            return await self._urgent_while_escalated(conversation, clinic)
        if conversation.status is not AiConversationStatus.ACTIVE:
            return ReplyOutcome(status="skipped")
        last = await self._last_patient_message(conversation)
        if last is None:
            return ReplyOutcome(status="skipped", note="нет сообщений пациента")
        # Ф8: после последнего хода пациента уже есть наш — отвечать нечего.
        # Так повторное или отложенное задание не даёт второго ответа.
        if await self._repository.answered_after(conversation.id, last.id):
            return ReplyOutcome(status="skipped", note="already_answered")
        self._last_patient_at = last.created_at

        # §10.2: язык ответа — язык последнего сообщения пациента. Переключаем
        # диалог сразу, до фильтров: шаблоны медфильтра и «уточню у
        # администратора» тоже должны уйти на языке, которым он сейчас пишет.
        # Слабая догадка (одно слово) идущий диалог не переключает.
        guess = guess_lang(last.transcript or last.body)
        await self._follow_lang(conversation, guess)

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

        await self._show_typing(clinic, last)
        try:
            return await self._converse(conversation, clinic, self._provider, guess=guess)
        except AiProviderUnavailableError as exc:
            # Временно недоступная модель (Ф8): ход откладывает и повторяет
            # координатор задания — эскалация здесь стала бы лавиной.
            logger.warning("ai_provider_unavailable", error=str(exc))
            await self._session.commit()
            return ReplyOutcome(status="deferred", note=str(exc))

    async def _urgent_while_escalated(
        self, conversation: AiConversation, clinic: Clinic
    ) -> ReplyOutcome:
        """Неотложное в диалоге у администратора — шаблон §9.2 сразу.

        Бот в переданном диалоге молчит, а возвращается к нему только через
        15 минут после сообщения пациента (решение 15.10.2026). Для «сильное
        кровотечение» это слишком долго: раньше старая передача возвращалась
        боту на ближайшем тике, и шаблон с телефоном клиники уходил за минуту.
        Поэтому неотложное разбирается и здесь: телефон клиники пациенту,
        ``ai.urgent`` (critical) администратору. Всё остальное ждёт человека.
        """
        last = await self._last_patient_message(conversation)
        if last is None or await self._repository.answered_after(conversation.id, last.id):
            return ReplyOutcome(status="skipped")
        if _unreadable_reason(last) is not None:
            return ReplyOutcome(status="skipped")
        medical = classify(last.transcript or last.body)
        if medical.kind is not MedicalKind.URGENT:
            return ReplyOutcome(status="skipped")
        await self._follow_lang(conversation, guess_lang(last.transcript or last.body))
        return await self._urgent(conversation, clinic, medical)

    async def _show_typing(self, clinic: Clinic, message: AiMessage) -> None:
        """«Печатает…» пациенту, пока модель думает (решение 15.10.2026).

        Ход с подбором времени занимает 10–15 секунд, и всё это время пациент
        видел только серые галочки — будто его не слышат. Сообщение заодно
        отмечается прочитанным. Только в ``auto``: в «Проверяю» и теневом
        режиме отвечает человек, и «прочитано ботом» было бы неправдой.
        Сбой индикатора ход не задерживает — клиент глушит его сам.
        """
        if clinic.ai_mode is not AiMode.AUTO or not message.wa_message_id:
            return
        binding = await self._whatsapp.binding_by_clinic(clinic.id)
        if binding is None or not binding.active:
            return
        await client_for(self._wa, binding).send_typing(
            binding.phone_number_id, message.wa_message_id
        )

    async def _converse(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        provider: AiProvider,
        *,
        guess: LangGuess | None = None,
    ) -> ReplyOutcome:
        """Ход модели с инструментами → разбор → валидатор → доставка.

        :class:`AiProviderUnavailableError` отсюда пробрасывается: решение «отложить
        и повторить» принимает координатор задания, а не этот слой.

        Если в ходе уже ушла кнопка подтверждения, а текст не получился (снят
        валидатором, не разобран, модель сбилась), пациенту уходит подсказка
        нажать кнопку, а не «уточню у администратора» (:meth:`_button_reply`).
        """
        # §10.2: язык хода — язык последнего сообщения пациента (детектор уже
        # переключил на него диалог). Не распознан — язык диалога. Язык
        # выбирает модель сама, а V07 не применяется: до первого ответа бота
        # (если сообщение без признаков) и когда детектор не уверен. Переписка
        # 20.10.2026: на «Дагы бир запись кошуп койосузбу» модель ответила
        # по-кыргызски, а V07 по ошибочной догадке детектора заставил
        # переписать ответ на русский. Выбор модели потом запоминается.
        first_reply = await self._repository.bot_replies_count(conversation.id) == 0
        self._model_picks_lang = (first_reply and guess is None) or (
            guess is not None and not guess.strong
        )
        lang: str | None = None if self._model_picks_lang else _lang_code(conversation.lang)
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
        #: Кнопка подтверждения, уже ушедшая пациенту в этом ходу.
        button: _SentButton | None = None
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
                if (
                    call.name in BUTTON_TOOLS
                    and isinstance(outcome, dict)
                    and outcome.get("buttonSent") is True
                ):
                    confirmation = outcome.get("confirmationId")
                    button = _SentButton(call.name, str(confirmation) if confirmation else None)
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
                if button is not None:
                    return await self._button_reply(conversation, clinic, button, "tool loop limit")
                return await self._escalate(
                    conversation, clinic, "tool_error", {"note": "tool loop limit"}
                )
        except AiProviderError as exc:
            # Кнопка уже у пациента — откладывать ход нельзя: повтор пропустил
            # бы его как отвеченный, а последняя попытка (give_up) ответила бы
            # «уточню у администратора» при живой кнопке.
            if button is not None:
                return await self._button_reply(conversation, clinic, button, f"provider: {exc}")
            if isinstance(exc, AiProviderUnavailableError):
                raise
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
            if button is not None:
                return await self._button_reply(conversation, clinic, button, "no text")
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": "модель не дала текста"}
            )

        facts = await self.turn_facts(clinic, system, parts)
        try:
            reply, verdict = await self._parse_and_validate(
                conversation, system, parts, raw_text, lang, facts
            )
        except AiProviderError as exc:
            # Перегенерация упёрлась в провайдера — раньше это роняло задание.
            if button is not None:
                return await self._button_reply(conversation, clinic, button, f"provider: {exc}")
            if isinstance(exc, AiProviderUnavailableError):
                raise
            logger.warning("ai_provider_error", error=str(exc))
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": f"provider: {exc}"}
            )
        except _UnparseableReplyError as exc:
            if button is not None:
                return await self._button_reply(conversation, clinic, button, "unparseable")
            return await self._escalate(
                conversation,
                clinic,
                "validator_blocked",
                # Хвосты сырого текста — иначе «непарсибельный» нечем разбирать.
                {"note": "непарсибельный ответ", "raw": exc.raw[:300], "firstRaw": raw_text[:300]},
            )
        if verdict.action is VerdictAction.BLOCK:
            return await self._blocked(
                conversation, clinic, reply, verdict, model_name, button=button
            )
        if verdict.action is VerdictAction.ESCALATE:
            if button is not None and set(verdict.codes) <= _FORMAT_CODES:
                # Язык или длина не исправились повтором — это не просьба о
                # человеке: кнопка у пациента, достаточно подсказки нажать её.
                await self._save_bot_message(conversation, reply, verdict, model_name, None, None)
                return await self._button_reply(
                    conversation, clinic, button, f"format: {', '.join(verdict.codes)}"
                )
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
                message.role in (AiMessageRole.BOT, AiMessageRole.ADMIN, AiMessageRole.SYSTEM)
                and message.sent_at is not None
            ):
                # Всё, что клиника отправила пациенту, — «наша сторона» диалога:
                # ответы бота и человека, а также служебные сообщения — «уточню
                # у администратора», кнопка и «готово, вы записаны», напоминания.
                # Инцидент 13.10.2026: служебных модель не видела и, не зная о
                # подтверждённой записи, предлагала вторую, а на «Здравствуйте»
                # после «позовите админа» снова звала человека.
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
        Запреты (V04) не повторяются — это не опечатка. Сигналы первой попытки
        (V09) остаются в итоговом вердикте, даже если повтор прошёл.

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
                        first_verdict = replace(first_verdict, action=VerdictAction.ESCALATE)
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
                    verdict = replace(verdict, action=VerdictAction.ESCALATE)
                if retried and verdict.action is VerdictAction.PASS:
                    # Метка в журнале: ответ прошёл со второй попытки.
                    verdict = replace(verdict, notes=f"после повтора: {', '.join(retried)}")
                if retried:
                    # Коды первой попытки — при ЛЮБОМ исходе повтора: выдумку,
                    # после которой модель сказала «не знаю», лестница доверия
                    # тоже обязана посчитать фактической ошибкой.
                    verdict = replace(verdict, retried_codes=retried)
                if first is not None and first[1].signals:
                    # Сигнал первой попытки (V09) не теряется, даже если
                    # переписанный ответ чист: выдумку модель всё равно сделала.
                    verdict = replace(verdict, signals={**first[1].signals, **verdict.signals})
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
                # Ответ модели не теряется: сотрудник видит в чате, что хотел
                # сказать бот и почему WhatsApp это не принял (16.10.2026).
                failure = _failure_of(exc)
                message = await self._save_bot_message(
                    conversation, reply, verdict, model_name, None, None
                )
                message.delivery_error = failure.text
                return await self._delivery_failed(
                    conversation, clinic, failure, proposed_message_id=message.id
                )
            sent_at = datetime.now(UTC)
            wa_message_id = sent.wa_message_id
            conversation.last_msg_at = sent_at
            await self._remember_lang(conversation, reply, sent=True)

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
        *,
        button: _SentButton | None = None,
    ) -> ReplyOutcome:
        """Валидатор заблокировал: ответ в журнал (sent_at=null) + эскалация.

        Кнопка подтверждения в этом ходу уже ушла — вместо эскалации подсказка
        нажать её: снятый текст лишь пересказывал кнопку (:meth:`_button_reply`).
        """
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
        if button is not None:
            return await self._button_reply(
                conversation, clinic, button, f"validator: {', '.join(verdict.codes)}"
            )
        return await self._escalate(
            conversation,
            clinic,
            "validator_blocked",
            {"codes": verdict.codes, "notes": verdict.notes, "proposed": reply.reply},
            proposed_message_id=message.id,
        )

    async def _button_reply(
        self, conversation: AiConversation, clinic: Clinic, button: _SentButton, why: str
    ) -> ReplyOutcome:
        """Кнопка уже у пациента, а текст хода не вышел — подсказка вместо передачи.

        Инцидент 13.10.2026: кнопка записи ушла, валидатор снял текст, и
        пациент получил одновременно кнопку и «уточню у администратора»; он
        нажал её и записался, а диалог висел у администратора со срочным пушем
        без причины. Кнопка сама называет услугу и время, поэтому достаточно
        шаблона «нажмите «Подтвердить»». Шаблон не ушёл — прежняя передача
        человеку. Пациент успел нажать кнопку, пока шёл повтор, — подсказка
        после «готово, вы записаны» выглядела бы поломкой, её не шлём.
        """
        logger.warning(
            "ai_button_fallback", conversation_id=str(conversation.id), tool=button.tool, why=why
        )
        if await self._button_pressed(button):
            await self._session.commit()
            return ReplyOutcome(status="skipped", note="кнопка уже нажата")
        lang = _lang_code(conversation.lang)
        body = service_text("button_sent", lang).format(title=button_title(button.tool, lang))
        result = await self._send_filter_reply(conversation, clinic, body, note="button_fallback")
        if result.failure is not None:
            return await self._delivery_failed(conversation, clinic, result.failure)
        if not result.sent:
            return await self._escalate(
                conversation, clinic, "tool_error", {"note": f"после кнопки: {why}"}
            )
        await self._session.commit()
        return ReplyOutcome(status="sent", reply=body)

    async def _button_pressed(self, button: _SentButton) -> bool:
        """Нажал ли пациент кнопку: резерв ``hold:<uuid>`` уже израсходован."""
        raw = (button.confirmation_id or "").removeprefix(CONFIRM_PREFIX)
        try:
            hold_id = uuid.UUID(raw)
        except ValueError:
            return False
        consumed = (
            await self._session.execute(select(SlotHold.consumed_at).where(SlotHold.id == hold_id))
        ).scalar_one_or_none()
        return consumed is not None

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

        Шаблон пациенту уходит в ``assisted`` и ``auto`` (в shadow пациент бота
        ещё не видел — писать ему от имени клиники нельзя) и моделью не
        переводится; какой именно — решает :meth:`_handoff_notice`.
        ``notify_patient=False`` — когда пациенту уже написали своим шаблоном
        (лимит §14): два извинения подряд выглядят поломкой.

        Сотрудник только что закрыл передачу этого же сообщения пациента с той
        же причиной — вторую не заводим (:meth:`_hold_after_staff_return`).
        Другая причина (новая карточка записи, несовпадение личности) и причины
        из :data:`_ALWAYS_HAND_OFF` передаются как обычно. Проверка — здесь, а
        не на каждом ходе: передача редка, а запрос к ``ai_escalation`` — нет.
        """
        if (
            self._last_patient_at is not None
            and reason not in _ALWAYS_HAND_OFF
            and reason
            in await self._repository.staff_closed_reasons(
                conversation.id, since=self._last_patient_at
            )
        ):
            return await self._hold_after_staff_return(conversation, clinic, reason)
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
        if notify_patient and _speaks_to_patient(clinic):
            notice = await self._handoff_notice(conversation, clinic, reason)
            if notice is not None:
                await self._send_notice(conversation, clinic, notice)
        await self._session.commit()
        return ReplyOutcome(status="escalated", escalation_reason=reason)

    async def _hold_after_staff_return(
        self, conversation: AiConversation, clinic: Clinic, reason: str
    ) -> ReplyOutcome:
        """Бот снова хочет передать человеку сообщение, по которому передачу уже закрыли.

        Инцидент 22–23.10.2026: пациент просит администратора, бот передаёт,
        сотрудник нажимает «вернуть боту», не написав пациенту, — и через три
        секунды бот передаёт тот же вопрос снова. Пациент за десять дней не
        получил ни слова. Сотрудник просьбу видел (сам закрыл передачу), так
        что вторая передача ничего не даёт: ни новой эскалации, ни пуша.
        Пациенту — «администратор уже в курсе», если это не последнее, что он
        от нас получил; диалог остаётся у бота.
        """
        logger.info(
            "ai_handoff_held_after_return", conversation_id=str(conversation.id), reason=reason
        )
        if _speaks_to_patient(clinic):
            notice = await self._handoff_notice(conversation, clinic, reason, waiting=True)
            if notice is not None:
                await self._send_notice(conversation, clinic, notice)
        await self._session.commit()
        return ReplyOutcome(status="held", escalation_reason=reason, note="returned_by_staff")

    async def _send_notice(self, conversation: AiConversation, clinic: Clinic, notice: str) -> None:
        """Отправить пациенту служебную фразу передачи и записать её в диалог.

        Отказ WhatsApp не теряет фразу: она остаётся в чате с причиной отказа.
        """
        binding = await self._whatsapp.binding_by_clinic(clinic.id)
        if binding is None or not binding.active:
            return
        try:
            sent = await client_for(self._wa, binding).send_text(
                binding.phone_number_id, conversation.wa_phone, notice
            )
        except WhatsAppSendError as exc:
            logger.warning("ai_escalation_notice_failed", error=str(exc))
            unsent = await self._whatsapp.add_message(
                conversation,
                role=AiMessageRole.SYSTEM,
                body=notice,
                wa_message_id=None,
                sent_at=None,
            )
            unsent.delivery_error = _failure_of(exc).text
            return
        now = datetime.now(UTC)
        await self._whatsapp.add_message(
            conversation,
            role=AiMessageRole.SYSTEM,
            body=notice,
            wa_message_id=sent.wa_message_id,
            sent_at=now,
        )
        conversation.last_msg_at = now
        await self._signal_message(conversation)

    async def _delivery_failed(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        failure: DeliveryFailure,
        *,
        proposed_message_id: int | None = None,
    ) -> ReplyOutcome:
        """WhatsApp не принял наше сообщение — передать человеку с понятной причиной.

        Переписка 16.10.2026: Meta четырнадцать раз отклонила ответы одному
        пациенту (номер не в списке разрешённых), и каждое его сообщение и каждый
        возврат боту стоили обращения к модели и новой передачи «tool_error» без
        объяснения. Теперь:

        * постоянный отказ — причина ``delivery_failed``, «липкая»: автовозврат
          её не снимает, бот молчит и модель не зовётся, пока сотрудник не
          вернёт диалог сам (значит, причину исправили). Шаблон пациенту не
          шлём — он упёрся бы в тот же отказ;
        * временный (сеть, лимит Meta) — прежняя ``tool_error``: бот попробует
          снова после автовозврата.

        Объяснение отказа — в ``payload.note`` и у самого сообщения в чате.
        """
        reason = "delivery_failed" if failure.permanent else "tool_error"
        return await self._escalate(
            conversation,
            clinic,
            reason,
            {"note": failure.text, "waCode": failure.code},
            proposed_message_id=proposed_message_id,
            notify_patient=not failure.permanent,
        )

    async def _handoff_notice(
        self, conversation: AiConversation, clinic: Clinic, reason: str, *, waiting: bool = False
    ) -> str | None:
        """Что сказать пациенту при передаче человеку; ``None`` — ничего.

        Решение 15.10.2026 (переписка 13.10: одна и та же фраза шесть раз за
        час, в том числе на «позовите администратора»):

        * пациент сам просил человека — «позвал администратора», а не «уточню»;
        * последнее, что клиника ему написала, — уже наш шаблон передачи:
          короткое «администратор уже в курсе», а если и оно было последним —
          тишина, повторять нечего;
        * клиника сейчас закрыта — когда она снова на связи («завтра с 09:00»).

        ``waiting=True`` — администратор о просьбе уже знает (сам закрыл
        передачу): сразу «уже в курсе», без «позвал».
        """
        last = await self._last_clinic_message(conversation)
        if last is not None and is_handoff_notice(last.body, waiting_only=True):
            return None
        if waiting or (last is not None and is_handoff_notice(last.body)):
            key = "handoff_waiting"
        else:
            key = "handoff_asked" if reason == "patient_asked" else "handoff"
        lang = _lang_code(conversation.lang)
        notice = service_text(key, lang)
        zone = _clinic_zone(clinic)
        now = datetime.now(zone)
        opening = next_opening(await self._hours_for(clinic.id), now)
        if opening is not None:
            notice += service_text("after_hours", lang).format(
                when=opening_phrase(opening, lang, today=now.date())
            )
        return notice

    async def _last_clinic_message(self, conversation: AiConversation) -> AiMessage | None:
        """Последнее, что клиника отправила пациенту в этом эпизоде (бот, человек, служебное).

        Эпизод — окно WhatsApp (24 ч): диалог на номер один и постоянный, и
        «администратор уже в курсе» месячной давности не должно глушить
        передачу по новому обращению.
        """
        return (
            await self._session.execute(
                select(AiMessage)
                .where(
                    AiMessage.conversation_id == conversation.id,
                    AiMessage.role != AiMessageRole.PATIENT,
                    AiMessage.sent_at.is_not(None),
                    AiMessage.sent_at >= datetime.now(UTC) - WINDOW,
                )
                .order_by(AiMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _hours_for(self, clinic_id: uuid.UUID) -> Callable[[int], WorkingHours]:
        """График клиники по дням недели; дня нет в базе — график по умолчанию."""
        by_day = await ScheduleRepository(self._session).working_hours(clinic_id)
        return lambda weekday: by_day.get(weekday) or default_working_hours(weekday)

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
        self,
        conversation: AiConversation,
        clinic: Clinic,
        body: str,
        *,
        note: str = "medical_filter",
    ) -> _TemplateSend:
        """Отправить шаблон (не ответ модели) пациенту и записать его в диалог.

        Уходит в ``assisted`` и ``auto``: в теневом режиме пациент бота ещё не
        видел, писать ему от имени клиники нельзя. В ``assisted`` через
        администратора идут ответы МОДЕЛИ, а шаблон — текст клиники, проверять
        нечего: раньше пациент с неотложным состоянием в этом режиме не получал
        даже телефона клиники. Возвращает, ушло ли сообщение, а при отказе
        WhatsApp — его причину (она же остаётся у сообщения в чате).
        ``note`` — метка в журнале, кто написал текст: медфильтр, подсказка
        после кнопки.
        """
        sent_at: datetime | None = None
        wa_message_id: str | None = None
        failure: DeliveryFailure | None = None
        if _speaks_to_patient(clinic):
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
                    failure = _failure_of(exc)
        message = await self._whatsapp.add_message(
            conversation,
            role=AiMessageRole.BOT,
            body=body,
            wa_message_id=wa_message_id,
            sent_at=sent_at,
        )
        # Метка в журнале: этот ответ написал шаблон, а не модель.
        message.validator = {"passed": True, "codes": [], "notes": note}
        if failure is not None:
            message.delivery_error = failure.text
        if sent_at is not None:
            # Как и ответ модели: на экран — только то, что ушло пациенту.
            await self._signal_message(conversation)
        return _TemplateSend(sent=sent_at is not None, failure=failure)

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
        result = await self._send_filter_reply(conversation, clinic, body)
        # Бот замолкает: уточняющих вопросов при неотложном не задают.
        self._repository.add_escalation(
            conversation.id,
            conversation.clinic_id,
            reason="urgent_medical",
            payload={
                "trigger": medical.label,
                "proposedMessageId": None,
                **({"note": result.failure.text} if result.failure else {}),
            },
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
        """§9.1: состояние не обсуждаем — зовём к врачу и сразу даём слоты.

        Пациент уже записан на сегодня или завтра — новое время не предлагаем,
        а напоминаем его запись: врач посмотрит жалобу на приёме.
        """
        lang = _lang_code(conversation.lang)
        visit = await self._soon_visit(conversation, clinic)
        if visit is not None:
            today = datetime.now(_clinic_zone(clinic)).date()
            body = medical_text("complaint_booked", lang).format(
                visit=visit_phrase(visit.starts_at, visit.doctor_name, lang, today=today)
            )
        else:
            slots = await self._nearest_slots(clinic, lang)
            body = (
                medical_text("complaint", lang).format(slots=slots)
                if slots
                else medical_text("complaint_no_slots", lang)
            )
        result = await self._send_filter_reply(conversation, clinic, body)
        if result.failure is not None:
            # Раньше отказ WhatsApp здесь превращался в «shadow»: пациент без
            # ответа, и никто об этом не знал.
            return await self._delivery_failed(conversation, clinic, result.failure)
        await self._session.commit()
        logger.info(
            "ai_medical_complaint",
            conversation_id=str(conversation.id),
            trigger=medical.label,
            booked=visit is not None,
        )
        return ReplyOutcome(status="sent" if result.sent else "shadow", reply=body)

    async def _soon_visit(
        self, conversation: AiConversation, clinic: Clinic
    ) -> UpcomingVisit | None:
        """Ближайшая запись пациента на сегодня или завтра (по часам клиники).

        Инцидент 13.10.2026: пациент, записанный на 13:00, написал «сильно
        болит», и шаблон предложил ему 13:30, 13:45, 14:00 — как незнакомцу.
        Запись дальше завтрашнего дня не мешает предложить время раньше: с
        болью ждать неделю никто не должен.
        """
        if conversation.patient_id is None:
            return None
        zone = _clinic_zone(clinic)
        today = datetime.now(zone).date()
        until = datetime.combine(today + timedelta(days=2), time.min, tzinfo=zone)
        visits = await BookingService(self._session).upcoming_visits(
            clinic.id, conversation.patient_id, until=until, limit=1
        )
        return visits[0] if visits else None

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
        Пациент уже записан на сегодня или завтра — :meth:`_acute_pain_booked`.
        """
        lang = _lang_code(conversation.lang)
        visit = await self._soon_visit(conversation, clinic)
        if visit is not None:
            return await self._acute_pain_booked(conversation, clinic, medical, visit, lang)
        slots = await self._nearest_slots(clinic, lang)
        body = (
            medical_text("acute_pain", lang).format(slots=slots)
            if slots
            else medical_text("acute_pain_no_slots", lang)
        )
        result = await self._send_filter_reply(conversation, clinic, body)
        logger.info(
            "ai_medical_acute_pain",
            conversation_id=str(conversation.id),
            trigger=medical.label,
            has_slots=bool(slots),
            sent=result.sent,
        )
        if result.sent and slots:
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
            {
                "trigger": medical.label,
                "slots": slots or None,
                **({"note": result.failure.text} if result.failure else {}),
            },
            notify_patient=False,
        )

    async def _acute_pain_booked(
        self,
        conversation: AiConversation,
        clinic: Clinic,
        medical: MedicalVerdict,
        visit: UpcomingVisit,
        lang: str,
    ) -> ReplyOutcome:
        """Сильная боль у пациента с записью на сегодня или завтра.

        Пациенту — его запись, «администратор в курсе» и, если есть, свободное
        время раньше записи с вопросом о переносе. Администратору — тот же
        ``ai.urgent_booking`` с записью: решить, принять ли раньше, может только
        он. Шаблон не ушёл — передача человеку, как и без записи.
        """
        today = datetime.now(_clinic_zone(clinic)).date()
        starts = await BookingService(self._session).earliest_slots(clinic.id)
        slots = slots_phrase([s for s in starts if s < visit.starts_at], lang, today=today)
        body = medical_text("acute_pain_booked", lang).format(
            visit=visit_phrase(visit.starts_at, visit.doctor_name, lang, today=today)
        )
        if slots:
            body += medical_text("earlier_slots", lang).format(slots=slots)
        result = await self._send_filter_reply(conversation, clinic, body)
        logger.info(
            "ai_medical_acute_pain",
            conversation_id=str(conversation.id),
            trigger=medical.label,
            booked=True,
            has_slots=bool(slots),
            sent=result.sent,
        )
        payload = {
            "trigger": medical.label,
            "appointmentId": str(visit.appointment_id),
            "slots": slots or None,
            **({"note": result.failure.text} if result.failure else {}),
        }
        if result.sent:
            await emit_event(
                self._session,
                clinic_id=conversation.clinic_id,
                type_="ai.urgent_booking",
                entity_type="conversation",
                entity_id=conversation.id,
                payload=payload,
            )
            await self._session.commit()
            return ReplyOutcome(status="sent", reply=body)
        return await self._escalate(
            conversation, clinic, "acute_pain", payload, notify_patient=False
        )

    async def _follow_lang(self, conversation: AiConversation, guess: LangGuess | None) -> None:
        """Переключить язык диалога на язык последнего сообщения пациента (§10.2).

        * уверенная догадка переключает язык диалога всегда;
        * слабая — только пока бот ещё не отвечал: защищать нечего, а шаблоны
          медфильтра и передачи первого же хода должны уйти на языке пациента;
        * ``None`` (имя, число, «ок») язык не меняет.

        Профиль пациента — только по уверенной догадке и только если язык в нём
        ещё не задан: пациент глобальный, и одно сообщение в одной клинике не
        должно менять язык напоминаний во всех остальных.
        """
        if guess is None:
            return
        lang = _SPEC_TO_LANG.get(guess.lang)
        if lang is None:
            return
        if lang is not conversation.lang:
            if not guess.strong and await self._repository.bot_replies_count(conversation.id) > 0:
                return
            conversation.lang = lang
        if guess.strong and conversation.patient_id is not None:
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
                # V09 и прочие сигналы без блока: ответ ушёл, но нам есть что знать.
                **({"signals": verdict.signals} if verdict.signals else {}),
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

    async def _remember_lang(
        self, conversation: AiConversation, reply: BotReply, *, sent: bool = False
    ) -> None:
        """Запомнить язык ответа модели (§10.2).

        * первый ответ фиксирует язык диалога и профиля — как и раньше;
        * дальше — ход, где язык выбирала модель (детектор не уверен), но
          только когда ответ **ушёл** пациенту (``sent``): язык отклонённого
          черновика, теневого ответа или недоставленного не должен становиться
          языком шаблонов и напоминаний. Профиль пациента такой ход не трогает.
        """
        first = await self._repository.bot_replies_count(conversation.id) == 0
        if not first and not (self._model_picks_lang and sent):
            return
        lang = _SPEC_TO_LANG.get(reply.lang)
        if lang is None:
            return
        conversation.lang = lang
        if first and conversation.patient_id is not None:
            patient = await self._session.get(Patient, conversation.patient_id)
            if patient is not None and patient.preferred_lang is None:
                patient.preferred_lang = lang


def _failure_of(exc: WhatsAppSendError) -> DeliveryFailure:
    """Отказ Graph API → объяснение для чата и постоянство ошибки."""
    return describe_failure(status=exc.status, code=exc.code, detail=exc.detail)


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
