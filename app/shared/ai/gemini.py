"""Провайдер Gemini (Google AI Studio, REST v1beta) — function calling.

Бесплатный тир годится для теневого режима и пилота (лимиты на минуту/день).
⚠️ На бесплатном тире Google может использовать содержимое запросов для
улучшения своих продуктов — ТЗ §12.1 требует режим без хранения, поэтому до
реальных пациентов нужен платный тир либо осознанное решение владельца.

Структурированный ответ: пока идут инструменты — обычные ходы; финальный
текст модель обязана отдать строго JSON (контракт в системном промпте),
парсит его прикладной слой. Одновременный JSON-mime с tools у Gemini
ограничен — поэтому контракт промптом, а не generation_config.

Кэш префикса (Ф8): системный промпт клиники с инструментами — самая тяжёлая
и самая постоянная часть каждого запроса (правила, карточка, глоссарий, база
знаний до 30 000 токенов). Он кладётся в ``cachedContents`` Gemini один раз
на клинику и язык, а ходы ссылаются на него по имени: платятся только новые
токены. Реестр имён — в Redis по хэшу содержимого: правка базы знаний
меняет хэш, и следующий ход сам заведёт свежий кэш; старый истекает по TTL.
Слишком короткий префикс (лимит Google) или ошибка кэша — ход без кэша,
поведение ответа не меняется.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.shared.ai.provider import (
    AiMessagePart,
    AiProviderError,
    AiProviderUnavailableError,
    AiResult,
    AiToolDecl,
    ToolCallRequest,
)

logger = structlog.get_logger(__name__)

_TIMEOUT = 45.0
_API = "https://generativelanguage.googleapis.com/v1beta"
#: Реестр кэшей префикса: ``ai:prefix:<scope>:<hash>`` → имя ``cachedContents/…``.
PREFIX_KEY = "ai:prefix"
#: Запас между TTL кэша у Google и записью в Redis: истёкший кэш не должен
#: считаться живым ни секунды.
_TTL_MARGIN = 60


def _retry_after(resp: httpx.Response) -> float | None:
    """Секунды из ``Retry-After`` (числовая форма); иначе ``None`` — бэкофф свой."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _tools_payload(tools: list[AiToolDecl]) -> list[dict[str, Any]]:
    return [
        {
            "function_declarations": [
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools
            ]
        }
    ]


def _cache_rejected(resp: httpx.Response) -> bool:
    """Ответ говорит, что ссылка на кэш недействительна (истёк, удалён, чужой)."""
    return (
        resp.status_code in (400, 403, 404)
        and "cachedcontent" in resp.text.replace(" ", "").lower()
    )


class GeminiProvider:
    """REST-клиент ``generateContent`` с function calling и кэшем префикса."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        thinking_level: str = "",
        redis: Redis | None = None,
        cache_ttl_seconds: int = 1800,
        cache_enabled: bool = True,
    ) -> None:
        self._api_key = api_key
        self.model_name = model
        self._thinking_level = thinking_level
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._redis = redis
        self._cache_ttl = max(cache_ttl_seconds, _TTL_MARGIN * 2)
        self._cache_enabled = cache_enabled

    def _url(self) -> str:
        return f"{_API}/models/{self.model_name}:generateContent"

    @staticmethod
    def _contents(parts: list[AiMessagePart]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for part in parts:
            if part.role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": part.tool_name or "tool",
                                    "response": {"result": part.tool_result},
                                }
                            }
                        ],
                    }
                )
            elif part.role == "model" and part.tool_name is not None:
                call_part: dict[str, Any] = {
                    "functionCall": {
                        "name": part.tool_name,
                        "args": part.tool_args or {},
                    }
                }
                if part.tool_signature:
                    # Gemini 3.x: functionCall в истории обязан нести подпись
                    # рассуждения, иначе 400 «missing thought_signature».
                    call_part["thoughtSignature"] = part.tool_signature
                contents.append({"role": "model", "parts": [call_part]})
            else:
                contents.append(
                    {
                        "role": "model" if part.role == "model" else "user",
                        "parts": [{"text": part.text or ""}],
                    }
                )
        return contents

    async def complete(
        self,
        *,
        system: str,
        parts: list[AiMessagePart],
        tools: list[AiToolDecl],
        temperature: float,
        max_tokens: int,
        cache_scope: str | None = None,
    ) -> AiResult:
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if self._thinking_level:
            # «minimal» гасит размышления: у thinking-моделей мысли тратят
            # токены ответа — крошечный бюджет заканчивался ДО первого слова.
            generation_config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        base: dict[str, Any] = {
            "contents": self._contents(parts),
            "generationConfig": generation_config,
        }
        cache_key, cache_name = await self._prefix_cache(cache_scope, system, tools)
        if cache_name is not None:
            body = {**base, "cachedContent": cache_name}
            resp = await self._post(self._url(), body)
            if _cache_rejected(resp):
                # Кэш истёк или пропал у Google раньше, чем в реестре: забыть и
                # ответить без кэша — пациент задержки не заметит.
                logger.info("gemini_prefix_cache_lost", cache=cache_name)
                await self._forget(cache_key)
                cache_name = None
        if cache_name is None:
            body = {**base, "system_instruction": {"parts": [{"text": system}]}}
            if tools:
                body["tools"] = _tools_payload(tools)
            resp = await self._post(self._url(), body)
        self._raise_for_status(resp, "generateContent")
        return self._parse(resp.json())

    # ── HTTP ─────────────────────────────────────────────────────────────────

    async def _post(self, url: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(
                url, json=body, headers={"x-goog-api-key": self._api_key}
            )
        except httpx.HTTPError as exc:
            # Сеть и таймауты — временное (Ф8): ход откладывается, не эскалируется.
            raise AiProviderUnavailableError(f"Gemini: {exc}") from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        if resp.status_code == 429 or resp.status_code >= 500:
            # Троттлинг и сбои на стороне Google — тоже временное.
            logger.warning("gemini_throttled", status=resp.status_code, body=resp.text[:400])
            raise AiProviderUnavailableError(
                f"Gemini: HTTP {resp.status_code}", retry_after=_retry_after(resp)
            )
        if resp.status_code >= 400:
            # 400/401/403 повтором не лечатся: неверный запрос или ключ.
            logger.warning(
                "gemini_failed", what=what, status=resp.status_code, body=resp.text[:400]
            )
            raise AiProviderError(f"Gemini: HTTP {resp.status_code}")

    def _parse(self, data: dict[str, Any]) -> AiResult:
        try:
            candidate = data["candidates"][0]
            content_parts = candidate.get("content", {}).get("parts") or []
        except (KeyError, IndexError) as exc:
            raise AiProviderError("Gemini: пустой ответ") from exc

        usage_meta = data.get("usageMetadata") or {}
        usage = {
            "input": int(usage_meta.get("promptTokenCount") or 0),
            "output": int(usage_meta.get("candidatesTokenCount") or 0),
            # Токены, взятые из кэша префикса: входят в input, но тарифицируются иначе.
            "cached": int(usage_meta.get("cachedContentTokenCount") or 0),
        }
        for item in content_parts:
            call = item.get("functionCall")
            if call:
                return AiResult(
                    tool_call=ToolCallRequest(
                        name=str(call.get("name") or ""),
                        args=dict(call.get("args") or {}),
                        signature=item.get("thoughtSignature"),
                    ),
                    model=self.model_name,
                    usage=usage,
                )
        text = "".join(str(item.get("text") or "") for item in content_parts)
        if not text.strip():
            raise AiProviderError("Gemini: ответ без текста и без вызова инструмента")
        return AiResult(text=text, model=self.model_name, usage=usage)

    # ── Кэш префикса ─────────────────────────────────────────────────────────

    async def _prefix_cache(
        self, scope: str | None, system: str, tools: list[AiToolDecl]
    ) -> tuple[str | None, str | None]:
        """(ключ реестра, имя кэша) для этого префикса; (None, None) — без кэша.

        Ключ — хэш модели, промпта и инструментов: любое изменение = новый
        кэш. Неудача создания (короткий префикс, 400) помнится на TTL, чтобы
        не пытаться на каждом ходу; временный сбой (429/5xx/сеть) не помнится.
        """
        if not (scope and self._cache_enabled and self._redis is not None):
            return None, None
        fingerprint = "\n".join(
            [self.model_name, system, json.dumps(_tools_payload(tools), sort_keys=True)]
        )
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
        key = f"{PREFIX_KEY}:{scope}:{digest}"
        try:
            known = await self._redis.get(key)
            if known:
                return key, known.decode() if isinstance(known, bytes) else str(known)
            if await self._redis.exists(f"{key}:skip"):
                return key, None
        except RedisError as exc:
            logger.warning("gemini_prefix_cache_redis_failed", error=str(exc))
            return None, None
        try:
            name = await self._create_cache(scope, digest, system, tools)
        except AiProviderUnavailableError as exc:
            logger.info("gemini_prefix_cache_deferred", error=str(exc))
            return key, None
        except AiProviderError as exc:
            logger.info("gemini_prefix_cache_skipped", scope=scope, error=str(exc))
            await self._remember(f"{key}:skip", "1")
            return key, None
        await self._remember(key, name)
        return key, name

    async def _create_cache(
        self, scope: str, digest: str, system: str, tools: list[AiToolDecl]
    ) -> str:
        body: dict[str, Any] = {
            "model": f"models/{self.model_name}",
            "displayName": f"{scope}:{digest}",
            "systemInstruction": {"parts": [{"text": system}]},
            "ttl": f"{self._cache_ttl}s",
        }
        if tools:
            body["tools"] = _tools_payload(tools)
        resp = await self._post(f"{_API}/cachedContents", body)
        self._raise_for_status(resp, "cachedContents")
        data = resp.json()
        name = str(data.get("name") or "")
        if not name:
            raise AiProviderError("Gemini: кэш без имени")
        tokens = (data.get("usageMetadata") or {}).get("totalTokenCount")
        logger.info("gemini_prefix_cache_created", scope=scope, cache=name, tokens=tokens)
        return name

    async def _remember(self, key: str, value: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, value, ex=self._cache_ttl - _TTL_MARGIN)
        except RedisError as exc:
            logger.warning("gemini_prefix_cache_redis_failed", error=str(exc))

    async def _forget(self, key: str | None) -> None:
        if self._redis is None or key is None:
            return
        try:
            await self._redis.delete(key)
        except RedisError as exc:
            logger.warning("gemini_prefix_cache_redis_failed", error=str(exc))
