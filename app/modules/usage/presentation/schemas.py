"""Схемы учёта использования (админка Skrezo)."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field

from app.modules.usage.domain.rates import RateCard, UsageTotals
from app.shared.api.schemas import CamelModel


class UsageTotalsOut(CamelModel):
    """Счётчики за период: модель, ассистент, WhatsApp по категориям Меты.

    Все значения — целые счётчики (штуки или токены) за весь запрошенный период.
    Платные категории WhatsApp — те, что Мета выставляет в счёт; ответы в
    открытое окно 24 часов бесплатны и считаются отдельно.
    """

    model_calls: int = Field(
        description="Вызовов модели (ответы ассистента и резюме)", examples=[412]
    )
    model_input_tokens: int = Field(description="Токенов запроса суммарно", examples=[1_850_000])
    model_output_tokens: int = Field(description="Токенов ответа суммарно", examples=[96_400])
    model_cached_tokens: int = Field(
        default=0,
        description="Из токенов запроса взято из кэша префикса (Ф8) — они дешевле",
        examples=[1_210_000],
    )
    assistant_replies: int = Field(
        description="Ответов ассистента: отправленных, черновиков, теневых", examples=[318]
    )
    dialogs: int = Field(
        description="Диалогов с ассистентом — каждый считается один раз", examples=[74]
    )
    wa_sent: int = Field(
        description="Исходящих WhatsApp, по которым пришёл статус доставки", examples=[530]
    )
    wa_utility: int = Field(
        description="Платных сообщений категории utility (напоминания о приёмах)",
        examples=[120],
    )
    wa_marketing: int = Field(description="Платных сообщений категории marketing", examples=[0])
    wa_authentication: int = Field(
        description="Платных сообщений категории authentication (коды входа)", examples=[35]
    )
    wa_billable_other: int = Field(
        description="Платных сообщений, категорию которых Мета не сообщила", examples=[0]
    )
    wa_service_free: int = Field(
        description="Бесплатных: ответы пациенту в открытое окно 24 часов", examples=[375]
    )

    @classmethod
    def from_totals(cls, t: UsageTotals) -> UsageTotalsOut:
        return cls(**{name: getattr(t, name) for name in cls.model_fields})


class RateCardOut(CamelModel):
    """Прайс-лист из настроек, по которому считается смета (тыйыны).

    Ставки задаются переменными окружения сервера; нули — ставка не задана, и
    эта статья в смету не входит.
    """

    ai_input_per_mtok: int = Field(
        description="Тыйынов за миллион токенов запроса к модели", examples=[2600]
    )
    ai_output_per_mtok: int = Field(
        description="Тыйынов за миллион токенов ответа модели", examples=[21_500]
    )
    wa_utility: int = Field(description="Тыйынов за одно платное сообщение utility", examples=[90])
    wa_marketing: int = Field(
        description="Тыйынов за одно платное сообщение marketing", examples=[0]
    )
    wa_authentication: int = Field(
        description="Тыйынов за одно платное сообщение authentication", examples=[60]
    )

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
    """Счётчики клиники за один день (UTC)."""

    day: date = Field(description="День по UTC", examples=["2026-09-14"])
    totals: UsageTotalsOut = Field(description="Счётчики за этот день")


class ClinicUsageResponse(CamelModel):
    """Использование одной клиники за период: по дням и суммой, со сметой."""

    clinic_id: uuid.UUID = Field(description="Клиника, по которой построен отчёт")
    date_from: date = Field(description="Начало периода (UTC-день)", examples=["2026-09-01"])
    date_to: date = Field(description="Конец периода включительно", examples=["2026-09-30"])
    days: list[ClinicUsageDayOut] = Field(
        description="По дням, от ранних к поздним; дни без активности пропущены"
    )
    totals: UsageTotalsOut = Field(description="Сумма за весь период")
    estimated_cost_tiyin: int = Field(
        description="Смета себестоимости по прайс-листу, тыйыны; 0 — ставки не заданы",
        examples=[128400],
    )
    rate_card: RateCardOut = Field(description="Ставки, по которым посчитана смета")


class PlatformClinicUsageOut(CamelModel):
    """Строка платформенного отчёта: одна клиника за месяц."""

    clinic_id: uuid.UUID = Field(description="Клиника")
    clinic_name: str = Field(description="Название клиники", examples=["Дента+"])
    totals: UsageTotalsOut = Field(description="Счётчики клиники за месяц")
    estimated_cost_tiyin: int = Field(
        description="Смета клиники по прайс-листу, тыйыны; 0 — ставки не заданы",
        examples=[42_300],
    )


class PlatformUsageResponse(CamelModel):
    """Использование по всем клиникам за месяц — основа для цен и пакетов."""

    period: str = Field(description="Месяц YYYY-MM", examples=["2026-09"])
    clinics: list[PlatformClinicUsageOut] = Field(
        description=(
            "Клиники, у которых за месяц было хоть что-то, по названию; без активности не попадают"
        )
    )
    totals: UsageTotalsOut = Field(description="Сумма по всем клиникам")
    estimated_cost_tiyin: int = Field(
        description="Смета по всей платформе, тыйыны; 0 — ставки не заданы",
        examples=[1_284_000],
    )
    rate_card: RateCardOut = Field(description="Ставки, по которым посчитана смета")
