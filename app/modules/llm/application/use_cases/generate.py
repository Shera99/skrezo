"""Use-case: обращение голосового помощника приложения к модели (``/clinic/llm/generate``).

Порядок шагов не случаен:

1. размер и JSON, затем контракт тела (422) — неверный запрос не тратит ни
   лимит, ни квоту Google;
2. ключ не настроен — 503 ``llm_disabled``; предохранитель разомкнут —
   503 ``llm_unavailable`` сразу, без похода в Google;
3. лимит (429 ``llm_quota_exceeded``) — только для запроса, который уйдёт;
4. один вызов модели с таймаутом задачи, без повторов (дедлайн приложения);
5. учёт токенов в дневных счётчиках клиники (``voice_*``).

Сбой провайдера (таймаут, 5xx, отказ настройки) размыкает предохранитель после
серии; неверное тело (400 от Google) и квота Google (429) — нет: первое —
ошибка запроса, второе клиент получает честным 429 со сроком повтора.
Тексты запроса и ответа в журнал не попадают — только задача, модель,
токены, время и исход.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ServiceUnavailableError, TooManyRequestsError, ValidationAppError
from app.modules.llm.application.services.quota import LlmQuota
from app.modules.llm.domain.errors import (
    LlmRequestInvalidError,
    LlmUpstreamBusyError,
    LlmUpstreamError,
    LlmUpstreamRejectedError,
)
from app.modules.llm.domain.request import LlmTask, parse_request, upstream_body, usage_of
from app.modules.llm.infrastructure.gemini_client import GeminiGenerateClient
from app.modules.usage.application.services.usage_meter import UsageMeter
from app.shared.ai.resilience import CircuitBreaker

logger = structlog.get_logger(__name__)

REQUEST_INVALID = "llm_request_invalid"
REQUEST_REJECTED = "llm_request_rejected"
UPSTREAM_BUSY = "llm_upstream_busy"
UNAVAILABLE = "llm_unavailable"
DISABLED = "llm_disabled"

_UNAVAILABLE_MESSAGE = "Ассистент временно недоступен, попробуйте позже"
#: Если Google не подсказал срок, повтор — через минуту.
_DEFAULT_RETRY_AFTER = 60


@dataclass(frozen=True, slots=True)
class TaskSettings:
    """Модель, таймаут одного обращения и потолок ответа для задачи."""

    model: str
    timeout_seconds: float
    max_output_tokens: int


def task_settings(task: LlmTask) -> TaskSettings:
    """Настройки задачи из окружения: модель меняется без выпуска приложения."""
    if task is LlmTask.VOICE_AGENT:
        return TaskSettings(
            model=settings.llm_app_model_agent or settings.gemini_model,
            timeout_seconds=settings.llm_app_timeout_agent_seconds,
            max_output_tokens=settings.llm_app_max_output_tokens_agent,
        )
    return TaskSettings(
        model=settings.llm_app_model_classify or settings.gemini_model,
        timeout_seconds=settings.llm_app_timeout_classify_seconds,
        max_output_tokens=settings.llm_app_max_output_tokens_classify,
    )


class GenerateUseCase:
    def __init__(
        self,
        session: AsyncSession,
        client: GeminiGenerateClient | None,
        breaker: CircuitBreaker,
        quota: LlmQuota,
    ) -> None:
        self._session = session
        self._client = client
        self._breaker = breaker
        self._quota = quota
        self._meter = UsageMeter(session)

    async def execute(
        self, *, account_id: uuid.UUID, clinic_id: uuid.UUID, raw_body: bytes
    ) -> dict[str, Any]:
        """Проверить тело, вызвать модель, учесть токены; вернуть ответ Gemini как есть."""
        if len(raw_body) > settings.llm_app_max_body_bytes:
            raise ValidationAppError(
                f"Тело запроса больше {settings.llm_app_max_body_bytes} байт",
                code=REQUEST_INVALID,
            )
        try:
            body = json.loads(raw_body)
        except ValueError as exc:
            raise ValidationAppError("Тело запроса — не JSON", code=REQUEST_INVALID) from exc
        try:
            request = parse_request(body)
        except LlmRequestInvalidError as exc:
            raise ValidationAppError(str(exc), code=REQUEST_INVALID) from exc

        if self._client is None:
            raise ServiceUnavailableError(
                "Голосовой помощник не настроен на сервере", code=DISABLED
            )
        if await self._breaker.is_open():
            raise ServiceUnavailableError(_UNAVAILABLE_MESSAGE, code=UNAVAILABLE)
        await self._quota.consume(account_id, clinic_id)
        # Отпустить соединение с БД на время вызова модели: секунды ожидания
        # Google не должны держать транзакцию (idle in transaction за PgBouncer).
        await self._session.commit()

        config = task_settings(request.task)
        started = time.monotonic()
        try:
            data = await self._client.generate(
                config.model,
                upstream_body(
                    request,
                    max_output_tokens=config.max_output_tokens,
                    thinking_level=settings.gemini_thinking_level,
                ),
                timeout=config.timeout_seconds,
            )
        except LlmUpstreamRejectedError as exc:
            # Ошибка тела, а не провайдера: предохранитель не трогаем.
            self._log(request.task, clinic_id, config.model, started, outcome="rejected")
            raise ValidationAppError(
                f"Модель не приняла запрос: {exc}", code=REQUEST_REJECTED
            ) from exc
        except LlmUpstreamBusyError as exc:
            # Квота Google — не сбой: клиент получает честный 429 со сроком, а
            # предохранитель от всплеска одной клиники не глушит остальные.
            self._log(request.task, clinic_id, config.model, started, outcome="upstream_busy")
            retry_after = exc.retry_after or _DEFAULT_RETRY_AFTER
            raise TooManyRequestsError(
                "Ассистент перегружен, попробуйте позже",
                code=UPSTREAM_BUSY,
                extra={"retryAfter": retry_after},
                headers={"Retry-After": str(retry_after)},
            ) from exc
        except LlmUpstreamError as exc:
            await self._breaker.record_failure()
            self._log(request.task, clinic_id, config.model, started, outcome=type(exc).__name__)
            raise ServiceUnavailableError(_UNAVAILABLE_MESSAGE, code=UNAVAILABLE) from exc

        await self._breaker.record_success()
        usage = usage_of(data)
        try:
            await self._meter.voice_call(clinic_id, usage)
            await self._session.commit()
        except SQLAlchemyError as exc:
            # Ответ модели уже оплачен: сбой учёта не превращает его в 500 и не
            # провоцирует повтор (двойной расход) — теряем строку учёта, не ответ.
            await self._session.rollback()
            logger.error("llm_app_usage_failed", clinic_id=str(clinic_id), error=str(exc))
        self._log(request.task, clinic_id, config.model, started, outcome="ok", usage=usage)
        return data

    @staticmethod
    def _log(
        task: LlmTask,
        clinic_id: uuid.UUID,
        model: str,
        started: float,
        *,
        outcome: str,
        usage: dict[str, int] | None = None,
    ) -> None:
        """Журнал обращения — без текстов запроса и ответа."""
        logger.info(
            "llm_app_call",
            task=task.value,
            clinic_id=str(clinic_id),
            model=model,
            outcome=outcome,
            duration_ms=round((time.monotonic() - started) * 1000),
            input_tokens=(usage or {}).get("input"),
            output_tokens=(usage or {}).get("output"),
        )
