"""Прогон регрессии клиники (Ф6): без модели и сухим прогоном модели.

``offline`` — детерминированно и бесплатно: медфильтр, валидатор на снятой
выдумке против сегодняшних фактов, наличие выученного документа в промпте.
``model`` — каждый вопрос задаётся модели с сегодняшним промптом, базой знаний
и глоссарием, но без диалога: ничего не пишется пациенту и в переписку,
инструменты записи недоступны. Токены прогона учитываются клинике.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.ai.application.kb import KbService
from app.modules.ai.application.reply_service import ProcessAiReplyService
from app.modules.ai.application.tools import TOOL_DECLS, ToolContext, ToolExecutor
from app.modules.ai.domain.learning import RUN_MODES, Evaluation, evaluate_keywords
from app.modules.ai.domain.medical import MedicalKind, classify
from app.modules.ai.domain.prompt import PROMPT_VERSION
from app.modules.ai.domain.reply import BotReply, ReplyParseError, parse_reply
from app.modules.ai.domain.validator import VerdictAction, validate
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.ai.infrastructure.repositories.kb_repository import KbRepository
from app.modules.ai.infrastructure.repositories.regression_repository import RegressionRepository
from app.modules.usage.application.services.usage_meter import UsageMeter
from app.shared.ai import AiMessagePart, AiProvider, AiProviderError, AiProviderUnavailableError
from app.shared.db.enums import AiConversationStatus, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_regression_case import AiRegressionCase
from app.shared.db.models.ai_regression_result import AiRegressionResult
from app.shared.db.models.ai_regression_run import AiRegressionRun
from app.shared.db.models.clinic import Clinic
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

_NEEDS_MODEL = "нужна модель — прогон в режиме model"


@dataclass(slots=True)
class DryRun:
    """Итог сухого прогона одного вопроса.

    ``outcome``: reply | needs_human | blocked | regenerate | escalate |
    medical_urgent | medical_complaint | error.
    """

    outcome: str
    text: str = ""
    reply: BotReply | None = None
    codes: list[str] = field(default_factory=list)
    note: str | None = None


class RegressionRunner:
    def __init__(
        self, session: AsyncSession, provider: AiProvider | None, wa_client: WhatsAppCloudClient
    ) -> None:
        self._session = session
        self._provider = provider
        self._wa = wa_client
        self._repository = RegressionRepository(session)
        self._reply = ProcessAiReplyService(session, provider, wa_client)
        self._meter = UsageMeter(session)

    # ── Постановка ──────────────────────────────────────────────────────────

    async def create_run(
        self, clinic_id: uuid.UUID, *, mode: str, actor_id: uuid.UUID, trigger: str = "manual"
    ) -> AiRegressionRun:
        """Запись прогона; выполняет его задание воркера (или вызывающий на месте)."""
        if mode not in RUN_MODES:
            raise ValidationAppError("Режим прогона: offline или model", code="run_mode_unknown")
        active = await self._repository.active_run(
            clinic_id, within_seconds=settings.ai_regression_job_timeout
        )
        if active is not None:
            raise ConflictError(
                "Прогон уже идёт — дождитесь его завершения", code="regression_run_active"
            )
        run = AiRegressionRun(
            clinic_id=clinic_id,
            mode=mode,
            status="queued",
            trigger=trigger,
            triggered_by=actor_id,
            prompt_version=PROMPT_VERSION,
            model_name=self._provider.model_name if self._provider is not None else None,
        )
        self._session.add(run)
        await self._session.commit()
        return run

    # ── Выполнение ──────────────────────────────────────────────────────────

    async def run(self, run_id: uuid.UUID) -> str:
        """Оценить активные кейсы; повтор задания продолжает с неоценённых."""
        run = await self._session.get(AiRegressionRun, run_id)
        if run is None:
            raise NotFoundError("Прогон не найден", code="regression_run_not_found")
        if run.status in {"done", "failed"}:
            return f"прогон {run_id}: уже завершён ({run.status})"
        clinic = await self._session.get(Clinic, run.clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")

        run.status = "running"
        run.started_at = run.started_at or datetime.now(UTC)
        cases = await self._repository.active_cases(
            run.clinic_id, limit=settings.ai_regression_max_cases
        )
        done = await self._repository.evaluated_case_ids(run.id)
        run.total = len(cases)
        await self._session.commit()

        in_prompt = (
            await KbService(self._session, KbRepository(self._session)).usage(clinic.id)
        ).in_prompt
        aborted: str | None = None
        for case in cases:
            if case.id in done:
                continue
            started = time.monotonic()
            try:
                evaluation = await self._evaluate(run.mode, clinic, case, in_prompt)
            except AiProviderUnavailableError as exc:
                aborted = f"модель недоступна: {exc}"
                break
            self._session.add(
                AiRegressionResult(
                    run_id=run.id,
                    case_id=case.id,
                    status=evaluation.status,
                    expected=evaluation.expected[:2000],
                    actual=evaluation.actual[:2000],
                    note=evaluation.note,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            )
            case.last_result = evaluation.status
            case.last_run_at = datetime.now(UTC)
            setattr(run, evaluation.status, getattr(run, evaluation.status) + 1)
            # Итог каждого кейса — своим коммитом: упавшее задание не теряет сделанное.
            await self._session.commit()

        run.status = "failed" if aborted else "done"
        run.note = aborted
        run.finished_at = datetime.now(UTC)
        await self._session.commit()
        summary = (
            f"прогон {run.id} ({run.mode}): "
            f"{run.passed} ok / {run.failed} fail / {run.skipped} skip"
        )
        logger.info("ai_regression_run_finished", run_id=str(run.id), status=run.status)
        return summary if not aborted else f"{summary}; прерван: {aborted}"

    async def _evaluate(
        self, mode: str, clinic: Clinic, case: AiRegressionCase, in_prompt: set[uuid.UUID]
    ) -> Evaluation:
        expect: dict[str, Any] = dict(case.expect or {})
        if case.kind == "medical":
            expected = str(expect.get("medical") or "none")
            actual = classify(case.question).kind.value
            return Evaluation("passed" if actual == expected else "failed", expected, actual)
        if mode == "offline":
            return await self._offline(clinic, case, expect, in_prompt)
        if self._provider is None:
            return Evaluation("skipped", self._label(case, expect), "", note="модель не настроена")
        return await self._with_model(clinic, case, expect)

    async def _offline(
        self,
        clinic: Clinic,
        case: AiRegressionCase,
        expect: dict[str, Any],
        in_prompt: set[uuid.UUID],
    ) -> Evaluation:
        if case.kind == "knowledge":
            raw_id = expect.get("kbDocumentId")
            if not raw_id:
                return Evaluation("skipped", self._label(case, expect), "", note=_NEEDS_MODEL)
            document = await KbRepository(self._session).get(clinic.id, uuid.UUID(str(raw_id)))
            if document is None:
                return Evaluation("failed", "документ в промпте", "документ удалён")
            if not document.is_active:
                return Evaluation("failed", "документ в промпте", "документ выключен")
            if document.lang != case.lang:
                return Evaluation(
                    "failed", "документ в промпте", f"документ на языке {document.lang}"
                )
            if document.id not in in_prompt:
                return Evaluation(
                    "failed", "документ в промпте", "не помещается в промпт (порог 30 000 токенов)"
                )
            return Evaluation("passed", "документ в промпте", f"«{document.title}» активен")
        if case.kind == "refusal":
            proposed = str(expect.get("proposed") or "")
            if not proposed:
                return Evaluation("skipped", "отказ или факты", "", note=_NEEDS_MODEL)
            system = await self._reply.system_prompt(clinic, case.lang, None)
            facts = await self._reply.turn_facts(
                clinic, system, [AiMessagePart(role="user", text=case.question)]
            )
            verdict = validate(
                BotReply(
                    reply=proposed,
                    lang=case.lang,
                    intent="info",
                    used_facts=[],
                    confidence="high",
                    needs_human=False,
                    human_reason=None,
                ),
                case.lang,
                facts,
            )
            refused = verdict.action is not VerdictAction.PASS
            return Evaluation(
                "passed" if refused else "failed",
                "валидатор снимает выдумку",
                f"{verdict.action.value}/{','.join(verdict.codes) or '—'}",
                note=verdict.notes,
            )
        return Evaluation("skipped", self._label(case, expect), "", note=_NEEDS_MODEL)

    async def _with_model(
        self, clinic: Clinic, case: AiRegressionCase, expect: dict[str, Any]
    ) -> Evaluation:
        dry = await self.dry_run(clinic, case.question, case.lang)
        label = self._label(case, expect)
        if case.kind == "refusal":
            if dry.outcome == "error":
                return Evaluation("failed", label, dry.note or "ошибка")
            if dry.outcome == "reply":
                return Evaluation("passed", label, dry.text[:500], note="ответил с опорой на факты")
            return Evaluation("passed", label, dry.outcome, note=dry.note)
        # knowledge | reply: нужен ответ, узнаваемый по ключевым словам.
        if dry.outcome != "reply":
            return Evaluation("failed", label, dry.outcome, note=dry.note or dry.text[:300])
        keywords = [str(k) for k in (expect.get("keywords") or [])]
        return evaluate_keywords(dry.text, keywords, expected_label=label)

    @staticmethod
    def _label(case: AiRegressionCase, expect: dict[str, Any]) -> str:
        if case.kind == "refusal":
            return "отказ или ответ с опорой на факты"
        if case.kind == "reply":
            return str(expect.get("reply") or ", ".join(expect.get("keywords") or []))[:500]
        return "ключевые слова: " + ", ".join(str(k) for k in (expect.get("keywords") or []))

    # ── Сухой прогон ─────────────────────────────────────────────────────────

    async def dry_run(self, clinic: Clinic, question: str, lang: str) -> DryRun:
        """Один ход модели без диалога: промпт клиники, только инструменты чтения."""
        medical = classify(question)
        if medical.kind is not MedicalKind.NONE:
            return DryRun(outcome=f"medical_{medical.kind.value}", note=medical.label)
        if self._provider is None:
            return DryRun(outcome="error", note="модель не настроена")

        system = await self._reply.system_prompt(clinic, lang, None)
        parts = [AiMessagePart(role="user", text=question)]
        executor = ToolExecutor(self._session, AiRepository(self._session))
        # Диалог-пустышка вне сессии: инструментам чтения нужны только клиника и язык.
        context = ToolContext(
            conversation=AiConversation(
                clinic_id=clinic.id,
                patient_id=None,
                wa_phone="+000000000000",
                lang=Lang.KG if lang == "ky" else Lang.RU,
                status=AiConversationStatus.ACTIVE,
            ),
            clinic=clinic,
            wa_client=self._wa,
            binding_phone_number_id=None,
        )
        raw_text: str | None = None
        try:
            for _ in range(settings.ai_max_tool_calls + 1):
                await self._session.commit()  # соединение свободно на время модели
                result = await self._provider.complete(
                    system=system,
                    parts=parts,
                    tools=TOOL_DECLS,
                    temperature=settings.ai_temperature,
                    max_tokens=settings.ai_max_tokens,
                )
                await self._meter.model_call(clinic.id, result.usage)
                if result.tool_call is None:
                    raw_text = result.text
                    break
                call = result.tool_call
                if call.name == "escalate":
                    return DryRun(
                        outcome="escalate", note=str(call.args.get("reason") or "patient_asked")
                    )
                tool_result = await executor.dry_dispatch(context, call.name, call.args)
                parts.append(
                    AiMessagePart(
                        role="model",
                        tool_name=call.name,
                        tool_args=call.args,
                        tool_signature=call.signature,
                    )
                )
                parts.append(
                    AiMessagePart(role="tool", tool_name=call.name, tool_result=tool_result)
                )
            else:
                return DryRun(outcome="error", note="модель зациклилась на инструментах")
        except AiProviderUnavailableError:
            raise
        except AiProviderError as exc:
            return DryRun(outcome="error", note=f"модель: {exc}")
        if raw_text is None:
            return DryRun(outcome="error", note="модель не дала текста")
        try:
            reply = parse_reply(raw_text)
        except ReplyParseError as exc:
            return DryRun(outcome="error", text=raw_text[:300], note=f"не JSON: {exc}")
        facts = await self._reply.turn_facts(clinic, system, parts)
        verdict = validate(reply, lang, facts)
        outcome = {
            VerdictAction.PASS: "reply",
            VerdictAction.BLOCK: "blocked",
            VerdictAction.ESCALATE: "needs_human",
            VerdictAction.REGENERATE: "regenerate",
        }[verdict.action]
        return DryRun(
            outcome=outcome,
            text=reply.reply,
            reply=reply,
            codes=list(verdict.codes),
            note=(",".join(verdict.codes) + (f": {verdict.notes}" if verdict.notes else ""))
            or None,
        )
