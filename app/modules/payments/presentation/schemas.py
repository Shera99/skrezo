"""Схемы платежей (контракт фронта).

v1 — заглушки формы: реального журнала платежей нет (биллинг/провайдер
отложены), поэтому список пуст, а сводка нулевая. Форма ответа соответствует
контракту, чтобы фронт завёлся без изменений типов.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.shared.api.schemas import CamelModel


class PaymentSummary(CamelModel):
    period_label: str
    success_sum: int = 0
    success_count: int = 0
    failed_count: int = 0
    failed_sum: int = 0
    refund_sum: int = 0  # отрицательная
    refund_count: int = 0
    net: int = 0  # успешно − возвраты


class PaymentRow(CamelModel):
    id: str
    date: datetime
    client: str
    client_type: str = Field(description="solo | clinic")
    type: str = Field(description="«Продление» | «Активация» | «Ручная оплата» | «Возврат»")
    period: str
    amount: int  # отрицательная — возврат
    status: str = Field(description="success | failed | refund")
    method: str
    txn_id: str


class PaymentsBundle(CamelModel):
    summary: PaymentSummary
    rows: list[PaymentRow] = Field(default_factory=list)
    total_count: int = 0


class RefundRequest(CamelModel):
    reason: str = Field(min_length=1, description="Причина возврата")
