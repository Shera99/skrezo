"""Схемы учёта использования (админка Skrezo)."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field

from app.modules.usage.domain.rates import RateCard, UsageTotals
from app.shared.api.schemas import CamelModel


class UsageTotalsOut(CamelModel):
    """Счётчики за период: модель, ассистент, WhatsApp по категориям Меты."""

    model_calls: int = Field(description="Вызовов модели (ответы ассистента и резюме)")
    model_input_tokens: int = Field(description="Токенов запроса суммарно")
    model_output_tokens: int = Field(description="Токенов ответа суммарно")
    model_cached_tokens: int = Field(
        default=0, description="Из токенов запроса взято из кэша префикса (Ф8) — дешевле"
    )
    assistant_replies: int = Field(
        description="Ответов ассистента: отправленных, черновиков, теневых"
    )
    dialogs: int = Field(description="Диалогов с ассистентом — каждый считается один раз")
    wa_sent: int = Field(description="Исходящих WhatsApp, по которым пришёл статус доставки")
    wa_utility: int = Field(description="Платных сообщений категории utility (напоминания)")
    wa_marketing: int = Field(description="Платных категории marketing")
    wa_authentication: int = Field(description="Платных категории authentication")
    wa_billable_other: int = Field(description="Платных неизвестной категории")
    wa_service_free: int = Field(description="Бесплатных: ответы в открытое окно 24 часов")

    @classmethod
    def from_totals(cls, t: UsageTotals) -> UsageTotalsOut:
        return cls(**{name: getattr(t, name) for name in cls.model_fields})


class RateCardOut(CamelModel):
    """Прайс-лист из настроек, по которому считается смета (тыйыны)."""

    ai_input_per_mtok: int = Field(description="Тыйын за миллион токенов запроса")
    ai_output_per_mtok: int = Field(description="Тыйын за миллион токенов ответа")
    wa_utility: int = Field(description="Тыйын за платное utility-сообщение")
    wa_marketing: int
    wa_authentication: int

    @classmethod
    def from_rates(cls, r: RateCard) -> RateCardOut:
        return cls(
            ai_input_per_mtok=r.ai_input_per_mtok,
            ai_output_per_mtok=r.ai_output_per_mtok,
            wa_utility=r.wa_utility,
            wa_marketing=r.wa_marketing,
            wa_authentication=r.wa_authentication,
        )


class ClinicUsageDayOut(CamelModel):
    day: date
    totals: UsageTotalsOut


class ClinicUsageResponse(CamelModel):
    """Использование одной клиники за период: по дням и суммой, со сметой."""

    clinic_id: uuid.UUID
    date_from: date
    date_to: date
    days: list[ClinicUsageDayOut]
    totals: UsageTotalsOut
    estimated_cost_tiyin: int = Field(
        description="Смета себестоимости по прайс-листу, тыйыны; 0 — ставки не заданы",
        examples=[128400],
    )
    rate_card: RateCardOut


class PlatformClinicUsageOut(CamelModel):
    clinic_id: uuid.UUID
    clinic_name: str
    totals: UsageTotalsOut
    estimated_cost_tiyin: int


class PlatformUsageResponse(CamelModel):
    """Использование по всем клиникам за месяц — основа для цен и пакетов."""

    period: str = Field(description="Месяц YYYY-MM", examples=["2026-09"])
    clinics: list[PlatformClinicUsageOut]
    totals: UsageTotalsOut
    estimated_cost_tiyin: int
    rate_card: RateCardOut
