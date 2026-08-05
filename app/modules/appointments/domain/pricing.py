"""Арифметика чека: сумма позиций, скидка, итог к оплате.

Скидка задаётся **процентом** — так её ограничивает потолок роли
(``role.max_discount_percent``), который иначе нечем проверить до расчёта. А
хранится и показывается ещё и суммой: процент без суммы не сходится с кассой,
где всё в целых тыйынах.

Округление — «половина вверх», и считается один раз от всей суммы позиций, а не
по строкам: 10% от трёх позиций по 333 тыйына — это 100, а не 3 × 33 = 99.
Иначе итог чека зависел бы от того, как работы разбиты на строки.
"""

from __future__ import annotations


def discount_amount(subtotal_tiyin: int, percent: int) -> int:
    """Сумма скидки в тыйынах от ``subtotal_tiyin`` при ``percent``%."""
    if percent <= 0 or subtotal_tiyin <= 0:
        return 0
    return (subtotal_tiyin * percent + 50) // 100


def apply_discount(subtotal_tiyin: int, percent: int) -> tuple[int, int]:
    """Вернуть (сумма скидки, итог к оплате) для суммы позиций и процента."""
    discount = discount_amount(subtotal_tiyin, percent)
    return discount, subtotal_tiyin - discount
