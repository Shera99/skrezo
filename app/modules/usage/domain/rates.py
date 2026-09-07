"""Прайс-лист провайдеров и смета себестоимости — целые тыйыны.

Мета в статусах присылает категорию и признак платности сообщения, но не
сумму; модель отдаёт токены. В сомы это переводит прайс-лист, который правит
команда Skrezo, когда меняются ставки, — и смета пересчитывается задним
числом по тем же счётчикам. Ставки за токены — тыйыны за МИЛЛИОН токенов:
целые числа без дробных тыйын, как и везде в деньгах проекта.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class RateCard:
    """Тыйыны: за миллион токенов модели и за одно платное сообщение WhatsApp."""

    ai_input_per_mtok: int = 0
    ai_output_per_mtok: int = 0
    #: Токены из кэша префикса (Ф8) — своя, меньшая ставка.
    ai_cached_input_per_mtok: int = 0
    wa_utility: int = 0
    wa_marketing: int = 0
    wa_authentication: int = 0


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Сумма счётчиков за период (имена совпадают с колонками таблицы)."""

    model_calls: int = 0
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    #: Из них взято из кэша префикса (входят в model_input_tokens).
    model_cached_tokens: int = 0
    assistant_replies: int = 0
    dialogs: int = 0
    wa_sent: int = 0
    wa_utility: int = 0
    wa_marketing: int = 0
    wa_authentication: int = 0
    wa_billable_other: int = 0
    wa_service_free: int = 0

    def __add__(self, other: UsageTotals) -> UsageTotals:
        return UsageTotals(
            **{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)}
        )


def estimate_cost_tiyin(totals: UsageTotals, rates: RateCard) -> int:
    """Смета в тыйынах: токены по ставкам за миллион, сообщения — за штуку.

    Платные сообщения неизвестной категории считаются по utility-ставке —
    самой частой у клиники (напоминания) и потому наименее удивительной.
    """
    cached = min(totals.model_cached_tokens, totals.model_input_tokens)
    model = (
        (totals.model_input_tokens - cached) * rates.ai_input_per_mtok
        + cached * rates.ai_cached_input_per_mtok
        + totals.model_output_tokens * rates.ai_output_per_mtok
    ) // 1_000_000
    whatsapp = (
        totals.wa_utility * rates.wa_utility
        + totals.wa_marketing * rates.wa_marketing
        + totals.wa_authentication * rates.wa_authentication
        + totals.wa_billable_other * rates.wa_utility
    )
    return int(model + whatsapp)
