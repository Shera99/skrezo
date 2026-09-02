"""Структурированный ответ модели (§5 ТЗ): парсинг и словари значений.

Модель возвращает не текст, а JSON; свободный текст не принимается. Парсер
терпим к обёрткам (```json-заборы, преамбула) — но не к отсутствию структуры:
непарсибельный ответ → ход не состоялся (повтор, затем эскалация).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

#: Разрешённые намерения; незнакомое сводится к ``other``.
INTENTS = frozenset(
    {"info", "booking", "reschedule", "cancel", "complaint", "medical", "clarify", "other"}
)

_JSON_RE = re.compile(r"\{.*\}", re.S)


class ReplyParseError(Exception):
    """В ответе модели не нашлось валидного JSON нужной формы."""


@dataclass(frozen=True, slots=True)
class BotReply:
    """Ответ ассистента после разбора."""

    reply: str
    lang: str  # ru | ky (словарь спеки; наш kg маппится на границе)
    intent: str
    used_facts: list[str] = field(default_factory=list)
    confidence: str = "high"  # high | low
    needs_human: bool = False
    human_reason: str | None = None


def parse_reply(raw: str) -> BotReply:
    """Достать JSON из текста модели и собрать :class:`BotReply`."""
    match = _JSON_RE.search(raw)
    if match is None:
        raise ReplyParseError("нет JSON в ответе модели")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise ReplyParseError(f"битый JSON: {exc}") from exc
    reply = str(data.get("reply") or "").strip()
    if not reply:
        raise ReplyParseError("пустой reply")
    intent = str(data.get("intent") or "other")
    return BotReply(
        reply=reply,
        lang=str(data.get("lang") or "ru").lower(),
        intent=intent if intent in INTENTS else "other",
        used_facts=[str(f) for f in (data.get("used_facts") or [])],
        confidence=str(data.get("confidence") or "high").lower(),
        needs_human=bool(data.get("needs_human")),
        human_reason=(str(data["human_reason"]) if data.get("human_reason") else None),
    )
