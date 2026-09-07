"""Метрики обучения ассистента (Ф6) и гейт доверия (Ф7).

Числа, по которым видно, учится ли бот: доля ответов без человека, доля
правок черновиков, очередь «Обучение», «выучил за период». Гейт доверия —
из ТЗ: перевод на ступень выше возможен при N одобренных ответах и нуле
фактических ошибок (V01–V03) за последнюю неделю.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Коды валидатора, означающие фактическую ошибку: цена, время, врач не из фактов.
FACT_ERROR_CODES: tuple[str, ...] = ("V01", "V02", "V03")
#: Коды блокировки ответа (§7): факты, стоп-лист, конкретика без фактов, чужая клиника.
#: V06 («не знаю» модели) — не блок, а честный отказ: считается отдельно.
BLOCK_CODES: tuple[str, ...] = ("V01", "V02", "V03", "V04", "V05", "V09")
NEEDS_HUMAN_CODE = "V06"


def share(numerator: int, denominator: int) -> float:
    """Доля 0..1 с тремя знаками; пустой знаменатель — 0, а не ошибка."""
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


@dataclass(frozen=True, slots=True)
class TrustGate:
    """Готовность к следующей ступени доверия: «153 из 200» и чистая неделя."""

    approved: int
    required: int
    fact_errors: int
    fact_error_days: int

    @property
    def ready(self) -> bool:
        return self.approved >= self.required and self.fact_errors == 0

    @property
    def progress(self) -> float:
        return min(1.0, share(self.approved, self.required))


def trust_gate(
    approved: int, *, required: int, fact_errors: int, fact_error_days: int
) -> TrustGate:
    return TrustGate(
        approved=approved,
        required=max(1, required),
        fact_errors=fact_errors,
        fact_error_days=fact_error_days,
    )
