"""Ступени доверия ассистента (Ф7): Наблюдаю → Проверяю → Доверяю.

Ступень — это режим ``ai_mode`` глазами владельца: ``shadow`` / ``assisted`` /
``auto``; ``off`` — отдельный выключатель, не ступень. Вверх — только через
гейт (цифры, а не расписание: ТЗ §13.1), вниз — всегда и мгновенно.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.shared.db.enums import AiMode

LEVELS: tuple[str, ...] = ("shadow", "assisted", "auto")

LEVEL_TITLES: dict[str, tuple[str, str]] = {
    "shadow": (
        "Наблюдаю",
        "Бот готовит ответы, пациент их не видит — вы смотрите, как он справляется",
    ),
    "assisted": (
        "Проверяю",
        "Бот пишет черновик, отправляете вы — как есть или с правкой",
    ),
    "auto": (
        "Доверяю",
        "Бот отвечает сам после валидатора; человека зовёт по правилам",
    ),
}


@dataclass(frozen=True, slots=True)
class TrustStats:
    """Цифры, по которым открываются ступени."""

    shadow_replies: int
    approvals: int
    fact_errors: int
    fact_error_days: int


@dataclass(frozen=True, slots=True)
class Requirement:
    key: str
    label: str
    value: int
    required: int
    met: bool


@dataclass(frozen=True, slots=True)
class StepGate:
    level: str
    requirements: tuple[Requirement, ...]

    @property
    def ready(self) -> bool:
        return all(r.met for r in self.requirements)

    @property
    def blockers(self) -> list[str]:
        return [f"{r.label}: {r.value} из {r.required}" for r in self.requirements if not r.met]


def gate_for(
    level: str, stats: TrustStats, *, shadow_required: int, approvals_required: int
) -> StepGate:
    """Условия ступени. «Наблюдаю» открыта всегда; выше — цифры и чистая неделя."""
    clean_week = Requirement(
        "fact_errors",
        f"фактических ошибок за {stats.fact_error_days} дн. (нужно 0)",
        stats.fact_errors,
        0,
        stats.fact_errors == 0,
    )
    if level == "assisted":
        return StepGate(
            level,
            (
                Requirement(
                    "shadow_replies",
                    "ответов бота, прошедших валидатор в режиме «Наблюдаю»",
                    stats.shadow_replies,
                    shadow_required,
                    stats.shadow_replies >= shadow_required,
                ),
                clean_week,
            ),
        )
    if level == "auto":
        return StepGate(
            level,
            (
                Requirement(
                    "approvals",
                    "одобренных ответов в режиме «Проверяю»",
                    stats.approvals,
                    approvals_required,
                    stats.approvals >= approvals_required,
                ),
                clean_week,
            ),
        )
    return StepGate(level, ())


def level_index(mode: AiMode | str) -> int:
    """Позиция на лестнице; ``off`` — ниже первой ступени."""
    value = mode.value if isinstance(mode, AiMode) else mode
    return LEVELS.index(value) if value in LEVELS else -1


def is_step_up(baseline: AiMode | str, target: AiMode | str) -> bool:
    return level_index(target) > level_index(baseline)


def next_level(mode: AiMode | str) -> str | None:
    index = level_index(mode)
    return LEVELS[index + 1] if index + 1 < len(LEVELS) else None
