"""Контракт ``POST /clinic/llm/generate``: что приложение вправе прислать в Gemini.

Тело собирает мобильное приложение (системную инструкцию и инструменты — тоже),
поэтому сервер проверяет его до обращения к Google. Без проверки любой
сотрудник с токеном получил бы наш ключ Gemini как бесплатный чат-бот, а
вложения (аудио, фото) уехали бы в Google мимо договорённости «распознавание
речи — только на устройстве».

Что пропускается (ответ фронта 12.10.2026):

* задача — ``voice_agent`` (цикл с инструментами) или ``voice_classify``
  (фраза → JSON без инструментов);
* поля верхнего уровня — ``task``, ``contents``, ``systemInstruction``,
  ``tools``, ``generationConfig``; модель выбирает сервер;
* части сообщений — только текст, вызов инструмента и его результат (плюс
  служебная подпись ``thoughtSignature`` и метка ``thought`` моделей Gemini 3,
  которые приложение возвращает в цикле как есть);
* инструменты — только объявления функций, не больше :data:`MAX_FUNCTIONS`;
* из ``generationConfig`` — формат ответа и температура; длину ответа,
  число вариантов и «размышления» ставит сервер.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from app.modules.llm.domain.errors import LlmRequestInvalidError


class LlmTask(enum.StrEnum):
    """Задача приложения — от неё зависят модель, таймаут и потолок ответа."""

    VOICE_AGENT = "voice_agent"
    VOICE_CLASSIFY = "voice_classify"


#: Записей в ``contents``: цикл агента из трёх обращений даёт семь.
MAX_CONTENTS = 12
#: Объявлений функций у агента (у приложения их девять).
MAX_FUNCTIONS = 16

_TOP_LEVEL = frozenset({"task", "contents", "systemInstruction", "tools", "generationConfig"})
_CONTENT_KEYS = frozenset({"role", "parts"})
_ROLES = frozenset({"user", "model"})
_PART_KEYS = frozenset({"text", "functionCall", "functionResponse", "thoughtSignature", "thought"})
_GENERATION_KEYS = frozenset({"responseMimeType", "responseSchema", "temperature"})
#: Внутренности вызова функции и её результата: без ``parts`` — Gemini 3
#: принимает внутри ответа функции вложения, а снимки на сервер не отправляются.
_CALL_KEYS = frozenset({"name", "id", "args"})
_RESPONSE_KEYS = frozenset({"name", "id", "response"})


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """Проверенный запрос приложения — только разрешённые поля."""

    task: LlmTask
    contents: list[dict[str, Any]]
    system_instruction: dict[str, Any] | None
    tools: list[dict[str, Any]] | None
    generation: dict[str, Any]


def parse_request(body: Any) -> LlmRequest:
    """Проверить тело приложения; нарушение — :class:`LlmRequestInvalidError` с причиной."""
    if not isinstance(body, dict):
        raise LlmRequestInvalidError("Тело запроса — JSON-объект")
    unknown = sorted(set(body) - _TOP_LEVEL)
    if unknown:
        raise LlmRequestInvalidError(f"Недопустимые поля: {', '.join(unknown)}")
    try:
        task = LlmTask(str(body.get("task")))
    except ValueError as exc:
        raise LlmRequestInvalidError("task — voice_agent или voice_classify") from exc

    contents = _contents(body.get("contents"))
    system_instruction = _system_instruction(body.get("systemInstruction"))
    tools = _tools(body.get("tools"), task)
    generation = _generation(body.get("generationConfig"))
    return LlmRequest(
        task=task,
        contents=contents,
        system_instruction=system_instruction,
        tools=tools,
        generation=generation,
    )


def upstream_body(
    request: LlmRequest, *, max_output_tokens: int, thinking_level: str
) -> dict[str, Any]:
    """Тело ``generateContent`` для Google: поля приложения + серверные ограничения."""
    generation: dict[str, Any] = {
        **request.generation,
        "maxOutputTokens": max_output_tokens,
        "candidateCount": 1,
    }
    if thinking_level:
        # «minimal» — быстрый ответ: дедлайн приложения 3–4 с на обращение.
        generation["thinkingConfig"] = {"thinkingLevel": thinking_level}
    body: dict[str, Any] = {"contents": request.contents, "generationConfig": generation}
    if request.system_instruction is not None:
        body["systemInstruction"] = request.system_instruction
    if request.tools:
        body["tools"] = request.tools
    return body


def usage_of(response: dict[str, Any]) -> dict[str, int]:
    """Токены ответа Gemini для учёта: «размышления» тарифицируются как ответ."""
    meta = response.get("usageMetadata")
    if not isinstance(meta, dict):
        return {"input": 0, "output": 0}
    return {
        "input": _int(meta.get("promptTokenCount")),
        "output": _int(meta.get("candidatesTokenCount")) + _int(meta.get("thoughtsTokenCount")),
    }


# ── Части тела ───────────────────────────────────────────────────────────────


def _contents(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise LlmRequestInvalidError("contents — непустой список сообщений")
    if len(value) > MAX_CONTENTS:
        raise LlmRequestInvalidError(f"contents — не больше {MAX_CONTENTS} сообщений")
    for index, content in enumerate(value):
        where = f"contents[{index}]"
        if not isinstance(content, dict):
            raise LlmRequestInvalidError(f"{where} — объект")
        unknown = sorted(set(content) - _CONTENT_KEYS)
        if unknown:
            raise LlmRequestInvalidError(f"{where}: недопустимые поля {', '.join(unknown)}")
        role = content.get("role")
        if role is not None and role not in _ROLES:
            raise LlmRequestInvalidError(f"{where}.role — user или model")
        _parts(content.get("parts"), where, text_only=False)
    return value


def _system_instruction(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LlmRequestInvalidError("systemInstruction — объект с parts")
    unknown = sorted(set(value) - _CONTENT_KEYS)
    if unknown:
        raise LlmRequestInvalidError(f"systemInstruction: недопустимые поля {', '.join(unknown)}")
    _parts(value.get("parts"), "systemInstruction", text_only=True)
    return value


def _parts(value: Any, where: str, *, text_only: bool) -> None:
    if not isinstance(value, list) or not value:
        raise LlmRequestInvalidError(f"{where}.parts — непустой список")
    allowed = frozenset({"text"}) if text_only else _PART_KEYS
    for index, part in enumerate(value):
        path = f"{where}.parts[{index}]"
        if not isinstance(part, dict) or not part:
            raise LlmRequestInvalidError(f"{path} — непустой объект")
        unknown = sorted(set(part) - allowed)
        if unknown:
            # inlineData/fileData сюда же: звук и снимки на сервер не отправляются.
            raise LlmRequestInvalidError(
                f"{path}: недопустимые поля {', '.join(unknown)} — разрешены "
                f"{', '.join(sorted(allowed))}"
            )
        if "text" in part and not isinstance(part["text"], str):
            raise LlmRequestInvalidError(f"{path}.text — строка")
        if "thoughtSignature" in part and not isinstance(part["thoughtSignature"], str):
            raise LlmRequestInvalidError(f"{path}.thoughtSignature — строка")
        if "thought" in part and not isinstance(part["thought"], bool):
            raise LlmRequestInvalidError(f"{path}.thought — true или false")
        if "functionCall" in part:
            _function(part["functionCall"], f"{path}.functionCall", _CALL_KEYS, payload="args")
        if "functionResponse" in part:
            _function(
                part["functionResponse"], f"{path}.functionResponse", _RESPONSE_KEYS, "response"
            )


def _function(value: Any, path: str, allowed: frozenset[str], payload: str) -> None:
    """Вызов функции или её результат: имя строкой, данные объектом, ничего лишнего."""
    if not isinstance(value, dict):
        raise LlmRequestInvalidError(f"{path} — объект")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise LlmRequestInvalidError(
            f"{path}: недопустимые поля {', '.join(unknown)} — разрешены "
            f"{', '.join(sorted(allowed))}"
        )
    if not isinstance(value.get("name"), str) or not value["name"]:
        raise LlmRequestInvalidError(f"{path}.name — непустая строка")
    if payload in value and not isinstance(value[payload], dict):
        raise LlmRequestInvalidError(f"{path}.{payload} — объект")


def _tools(value: Any, task: LlmTask) -> list[dict[str, Any]] | None:
    if value is None or value == []:
        return None
    if task is LlmTask.VOICE_CLASSIFY:
        raise LlmRequestInvalidError("У voice_classify инструментов нет — уберите tools")
    if not isinstance(value, list):
        raise LlmRequestInvalidError("tools — список")
    functions = 0
    for index, tool in enumerate(value):
        where = f"tools[{index}]"
        # Только свои функции: поиск Google и выполнение кода платные и делают
        # из помощника клиники поисковик общего назначения.
        if not isinstance(tool, dict) or set(tool) != {"functionDeclarations"}:
            raise LlmRequestInvalidError(f"{where} — только functionDeclarations")
        declarations = tool["functionDeclarations"]
        if not isinstance(declarations, list):
            raise LlmRequestInvalidError(f"{where}.functionDeclarations — список")
        for declaration in declarations:
            if not isinstance(declaration, dict) or not isinstance(declaration.get("name"), str):
                raise LlmRequestInvalidError(f"{where}: у каждой функции нужно имя name")
        functions += len(declarations)
    if functions > MAX_FUNCTIONS:
        raise LlmRequestInvalidError(f"tools — не больше {MAX_FUNCTIONS} функций")
    return value


def _generation(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LlmRequestInvalidError("generationConfig — объект")
    # Остальные ключи (длина ответа, «размышления», число вариантов) задаёт
    # сервер — присланные молча не учитываются.
    generation = {key: value[key] for key in _GENERATION_KEYS if key in value}
    temperature = generation.get("temperature")
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, int | float):
            raise LlmRequestInvalidError("generationConfig.temperature — число")
        generation["temperature"] = min(max(float(temperature), 0.0), 1.0)
    return generation


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except TypeError, ValueError:
        return 0
