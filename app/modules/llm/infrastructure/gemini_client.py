"""Клиент ``generateContent`` Gemini для голосового помощника: тело как есть.

Отдельный от :class:`app.shared.ai.gemini.GeminiProvider`: тот строит запрос
из типизированных частей ассистента пациентов, ждёт до 45 с и повторяет
вызовы. Здесь тело собрало приложение, ответ нужен за 4–6 с, и повтор съел
бы дедлайн — поэтому один вызов со своим таймаутом и разбор ошибок по смыслу.

Тексты запроса и ответа в лог не пишутся: там фразы врача и данные пациентов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.modules.llm.domain.errors import (
    LlmUpstreamBusyError,
    LlmUpstreamFailedError,
    LlmUpstreamRejectedError,
    LlmUpstreamTimeoutError,
)
from app.shared.http import shared_async_client

logger = structlog.get_logger(__name__)

_API = "https://generativelanguage.googleapis.com/v1beta"
#: Сколько символов сообщения Google об ошибке отдать клиенту — подсказка, не дамп.
_ERROR_PREVIEW = 300


class GeminiGenerateClient:
    """Один вызов ``models/{model}:generateContent`` с ключом приложения."""

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._client = client

    async def generate(self, model: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Ответ Gemini как есть; отказ — одна из ошибок :mod:`..domain.errors`."""
        url = f"{_API}/models/{model}:generateContent"
        try:
            resp = await self._client.post(
                url, json=body, headers={"x-goog-api-key": self._api_key}, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise LlmUpstreamTimeoutError(f"нет ответа за {timeout} с") from exc
        except httpx.HTTPError as exc:
            raise LlmUpstreamFailedError(f"сеть: {type(exc).__name__}") from exc

        if resp.status_code < httpx.codes.BAD_REQUEST:
            try:
                data = resp.json()
            except ValueError as exc:
                raise LlmUpstreamFailedError("ответ не JSON") from exc
            if not isinstance(data, dict):
                raise LlmUpstreamFailedError("ответ не объект")
            return data

        error = _GoogleError.parse(resp)
        if resp.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise LlmUpstreamBusyError(error.retry_after)
        if error.is_our_setup:
            # Ключ, биллинг, регион — поломка нашей настройки, а не тела приложения.
            # Решение — по структурированным полям ответа, НЕ по тексту: Google
            # повторяет в сообщении имена полей тела, и «API_KEY» в теле иначе
            # выдавал бы себя за неверный ключ и размыкал предохранитель всем.
            logger.error(
                "llm_app_setup_rejected",
                status=resp.status_code,
                google_status=error.status,
                reasons=sorted(error.reasons),
            )
            raise LlmUpstreamFailedError(f"настройка отклонена: {error.status or 'HTTP'}")
        if resp.status_code == httpx.codes.BAD_REQUEST:
            raise LlmUpstreamRejectedError(error.message)
        logger.warning("llm_app_upstream_failed", status=resp.status_code)
        raise LlmUpstreamFailedError(f"HTTP {resp.status_code}")


def build_llm_client() -> GeminiGenerateClient | None:
    """Клиент по настройкам; не настроен — ``None`` (ручка ответит 503 ``llm_disabled``).

    Свой ключ ``GEMINI_APP_API_KEY`` включает помощника сам. Общий
    ``GEMINI_API_KEY`` подхватывается, только если Gemini явно включён
    (``AI_PROVIDER=gemini``): выключатель ассистента — общий для всех контуров
    модели, и ключ бесплатного тира не должен незаметно начать получать фразы
    врачей и данные пациентов.
    """
    api_key = settings.gemini_app_api_key or (
        settings.gemini_api_key if settings.ai_provider == "gemini" else ""
    )
    if not api_key:
        return None
    # Свой пул соединений: TLS к Google не поднимается на каждый вопрос врача.
    timeout = max(settings.llm_app_timeout_agent_seconds, settings.llm_app_timeout_classify_seconds)
    return GeminiGenerateClient(api_key, shared_async_client("gemini-app", timeout=timeout))


#: Причины (``ErrorInfo.reason``), означающие поломку нашей настройки, а не тела.
_SETUP_REASONS = frozenset(
    {
        "API_KEY_INVALID",
        "API_KEY_EXPIRED",
        "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "API_KEY_IP_ADDRESS_BLOCKED",
        "BILLING_DISABLED",
        "SERVICE_DISABLED",
        "CONSUMER_INVALID",
    }
)
#: Статусы Google, которые тоже про настройку проекта (регион, биллинг, доступ).
_SETUP_STATUSES = frozenset({"FAILED_PRECONDITION", "PERMISSION_DENIED", "UNAUTHENTICATED"})
_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


@dataclass(frozen=True, slots=True)
class _GoogleError:
    """Разобранная ошибка Google API (``{"error": {code, message, status, details}}``)."""

    status: str
    reasons: frozenset[str]
    message: str
    retry_after: int | None

    @property
    def is_our_setup(self) -> bool:
        return bool(self.reasons & _SETUP_REASONS) or self.status in _SETUP_STATUSES

    @classmethod
    def parse(cls, resp: httpx.Response) -> _GoogleError:
        try:
            error = resp.json().get("error") or {}
        except ValueError, AttributeError:
            error = {}
        if not isinstance(error, dict):
            error = {}
        raw_details = error.get("details")
        details: list[Any] = raw_details if isinstance(raw_details, list) else []
        reasons = frozenset(
            str(item.get("reason"))
            for item in details
            if isinstance(item, dict) and item.get("reason")
        )
        retry_after = _header_retry_after(resp) or _detail_retry_after(details)
        message = str(error.get("message") or "") or f"HTTP {resp.status_code}"
        return cls(
            status=str(error.get("status") or ""),
            reasons=reasons,
            message=message[:_ERROR_PREVIEW],
            retry_after=retry_after,
        )


def _header_retry_after(resp: httpx.Response) -> int | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(1, round(float(raw)))
    except ValueError:
        return None


def _detail_retry_after(details: list[Any]) -> int | None:
    """Срок из ``RetryInfo.retryDelay`` («37s») — Gemini кладёт его в тело, не в заголовок."""
    for item in details:
        if not isinstance(item, dict) or not str(item.get("@type", "")).endswith("RetryInfo"):
            continue
        match = _RETRY_DELAY.match(str(item.get("retryDelay") or ""))
        if match:
            return max(1, round(float(match.group(1))))
    return None
