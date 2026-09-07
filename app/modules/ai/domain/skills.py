"""Матрица умений ассистента (Ф7): клиника × умение вместо одного режима.

Ступень доверия (``ai_mode``) — общий потолок; у каждого умения свой потолок
(``off`` — выключено, ``assisted`` — бот готовит, действие за человеком,
``auto`` — бот действует сам). Действующий уровень = min(ступень, потолок):
клиника в «Доверяю» может отвечать сама, но запись оставить за администратором.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.shared.db.enums import AiMode

SKILLS: tuple[str, ...] = ("booking", "changes")
SKILL_TITLES: dict[str, tuple[str, str]] = {
    "booking": ("Запись на приём", "Подбор времени и кнопка подтверждения записи"),
    "changes": ("Перенос и отмена", "Своих будущих записей пациента — по кнопке"),
}
CAP_LEVELS: tuple[str, ...] = ("off", "assisted", "auto")
DEFAULT_SKILLS: dict[str, str] = dict.fromkeys(SKILLS, "auto")

#: Инструменты, принадлежащие умению: при ``off`` модель их не видит.
SKILL_TOOLS: dict[str, frozenset[str]] = {
    "booking": frozenset({"create_patient", "propose_appointment"}),
    "changes": frozenset({"propose_reschedule", "propose_cancel"}),
}
#: Общие для записи и переноса: пропадают, только когда выключено и то и другое.
SHARED_TOOLS: frozenset[str] = frozenset({"get_available_days", "get_available_slots"})


def caps_from(raw: Mapping[str, Any] | None) -> dict[str, str]:
    """Потолки из JSONB клиники; неизвестное/битое значение — как «auto»."""
    caps = dict(DEFAULT_SKILLS)
    for skill in SKILLS:
        value = (raw or {}).get(skill)
        if value in CAP_LEVELS:
            caps[skill] = str(value)
    return caps


def effective_level(ai_mode: AiMode, cap: str) -> str:
    """Действующий уровень умения: ступень режет сверху, потолок — снизу."""
    if ai_mode is AiMode.OFF or cap == "off":
        return "off"
    if ai_mode is AiMode.SHADOW:
        return "shadow"
    if ai_mode is AiMode.ASSISTED:
        return "assisted"
    return cap


def effective_skills(ai_mode: AiMode, raw: Mapping[str, Any] | None) -> dict[str, str]:
    caps = caps_from(raw)
    return {skill: effective_level(ai_mode, caps[skill]) for skill in SKILLS}


def disabled_tools(effective: Mapping[str, str]) -> frozenset[str]:
    """Какие инструменты убрать из выдачи модели при таких уровнях."""
    disabled: set[str] = set()
    for skill, tools in SKILL_TOOLS.items():
        if effective.get(skill, "auto") == "off":
            disabled |= tools
    if all(effective.get(skill, "auto") == "off" for skill in SKILL_TOOLS):
        disabled |= SHARED_TOOLS
    return frozenset(disabled)


def skill_of_tool(name: str) -> str | None:
    for skill, tools in SKILL_TOOLS.items():
        if name in tools:
            return skill
    return None
