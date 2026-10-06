"""Правила журнала операций: виды, источники, остаток."""

from __future__ import annotations

from dataclasses import dataclass

KIND_CREDIT = "credit"
KIND_DEBIT = "debit"
KINDS = (KIND_CREDIT, KIND_DEBIT)

SOURCE_PROVIDER = "freedompay_payment"
SOURCE_TOPUP = "manual_topup"
SOURCE_CHARGE = "subscription_charge"
SOURCE_REFUND = "refund"
SOURCE_ADJUSTMENT = "adjustment"
#: Доплата за остаток оплаченного периода при переходе на пакет дороже.
SOURCE_PLAN_CHANGE = "plan_change"
SOURCES = (
    SOURCE_PROVIDER,
    SOURCE_TOPUP,
    SOURCE_CHARGE,
    SOURCE_REFUND,
    SOURCE_ADJUSTMENT,
    SOURCE_PLAN_CHANGE,
)

#: Расходы, которым можно увести остаток в минус: возврат денег клинике и
#: корректировка Skrezo — это исправление факта, а не покупка.
NEGATIVE_ALLOWED = frozenset({SOURCE_REFUND, SOURCE_ADJUSTMENT})

PURPOSE_SUBSCRIPTION = "subscription"
PURPOSE_TOPUP = "topup"


@dataclass(frozen=True, slots=True)
class InsufficientFundsError(Exception):
    """Остатка не хватает на расход — сколько не хватает, знает вызывающий."""

    balance_tiyin: int
    amount_tiyin: int

    @property
    def shortfall_tiyin(self) -> int:
        return self.amount_tiyin - self.balance_tiyin


def next_balance(balance: int, kind: str, amount: int, *, source: str) -> int:
    """Остаток после операции; расход сверх остатка — только для разрешённых источников."""
    if amount <= 0:
        raise ValueError("сумма операции должна быть положительной")
    if kind == KIND_CREDIT:
        return balance + amount
    if kind != KIND_DEBIT:
        raise ValueError(f"неизвестный вид операции {kind}")
    if balance < amount and source not in NEGATIVE_ALLOWED:
        raise InsufficientFundsError(balance, amount)
    return balance - amount
