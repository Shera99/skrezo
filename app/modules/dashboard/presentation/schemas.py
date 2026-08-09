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
    """Клиники, которые не работают и требуют решения.

    ``trialExpired`` — самая массовая строка: триал кончился, оплаты не было.
    Без неё такая клиника не попадала ни в активные, ни в риски, ни в отменённые
    и просто пропадала с экрана — то есть отток был не виден вовсе.
    """

    trial_expired: int = Field(description="Триал кончился, оплаты не было")
    awaiting_card: int = Field(description="Ждут привязки карты (гейт карты отключён)")
    past_due: int = Field(description="Оплаченный период кончился")
    blocked: int = Field(description="Заблокированы решением Skrezo")


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


# ── Тренды (§4.2) — ряды по месяцам под графики ─────────────────────────────
class MrrPoint(CamelModel):
    month: str = Field(description="Месяц YYYY-MM")
    value: int


class ActivePoint(CamelModel):
    month: str
    solo: int
    clinic: int


class LeadsPoint(CamelModel):
    month: str
    leads: int
    clients: int


class RatioBlock(CamelModel):
    solo: int
    clinic: int


class DashboardTrends(CamelModel):
    mrr: list[MrrPoint] = Field(description="MRR по месяцам (0 — биллинг отложен)")
    active_clients: list[ActivePoint] = Field(description="Активные клиенты (solo/clinic)")
    leads_to_clients: list[LeadsPoint] = Field(description="Заявки → клиенты по месяцам")
    solo_clinic_ratio: RatioBlock = Field(description="Текущий срез соло/клиника")
