"""Схемы дашборда (контракт фронта DashboardSummary).

Блоки, зависящие от отложенного биллинга (``mrr``, дельты к прошлому периоду),
возвращают нули/пустой ряд — форма сохранена для фронта.
"""

from __future__ import annotations

from pydantic import Field

from app.shared.api.schemas import CamelModel


class ActiveBlock(CamelModel):
    total: int
    solo: int
    clinic: int
    delta_pct: float = 0.0


class MrrBlock(CamelModel):
    value: int = 0
    delta_pct: float = 0.0
    delta_abs: int = 0
    spark: list[int] = Field(default_factory=list)


class TrialBlock(CamelModel):
    total: int
    no_card: int
    with_card: int


class RisksBlock(CamelModel):
    awaiting_card: int
    past_due: int
    blocked: int


class ConversionBlock(CamelModel):
    pct: float
    converted: int
    leads: int


class CancelledBlock(CamelModel):
    total: int
    delta_pct: float = 0.0
    solo: int
    clinic: int


class DashboardSummary(CamelModel):
    date_label: str
    active: ActiveBlock
    mrr: MrrBlock
    trial: TrialBlock
    risks: RisksBlock
    conversion: ConversionBlock
    cancelled: CancelledBlock
