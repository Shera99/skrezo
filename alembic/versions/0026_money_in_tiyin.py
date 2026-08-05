"""Деньги в тыйынах: единица хранения и обмена — 1/100 сома.

В целых сомах не выражается результат обычной скидки: 15% от 4250 = 637,5, и
эти полсома некуда деть — касса расходится на каждой скидке. Плюс исчезает
конвертация на границе API, которая давала расхождения.

Колонки переименованы с суффиксом ``_tiyin``, а не просто пересчитаны: колонка
``price``, хранящая тыйыны, — ловушка для следующего разработчика. Значения
умножаются на 100 (сомы → тыйыны), поэтому миграция **не идемпотентна**: повторный
прогон вверх-вниз-вверх сохранит суммы верно, но прерванный прогон — нет.

Revision ID: 0026_money_in_tiyin
Revises: 0025_appointment_arrived
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026_money_in_tiyin"
down_revision: str | None = "0025_appointment_arrived"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (таблица, старое имя, новое имя)
_COLUMNS: list[tuple[str, str, str]] = [
    ("appointment", "price_total", "price_total_tiyin"),
    ("appointment_tooth", "price", "price_tiyin"),
    ("cash_payment", "amount", "amount_tiyin"),
    ("service", "price", "price_tiyin"),
    ("tariff_version", "price", "price_tiyin"),
    ("tariff_version", "over_price", "over_price_tiyin"),
    ("tariff_package", "price", "price_tiyin"),
]
# Констрейнт ссылается на переименованную колонку — пересобираем.
_AMOUNT_CHECK = "ck_cash_payment_amount_positive"


def upgrade() -> None:
    op.drop_constraint(_AMOUNT_CHECK, "cash_payment", type_="check")
    for table, old, new in _COLUMNS:
        op.alter_column(table, old, new_column_name=new)
        op.execute(f"UPDATE {table} SET {new} = {new} * 100")  # сомы → тыйыны
    op.create_check_constraint(_AMOUNT_CHECK, "cash_payment", "amount_tiyin > 0")


def downgrade() -> None:
    op.drop_constraint(_AMOUNT_CHECK, "cash_payment", type_="check")
    for table, old, new in _COLUMNS:
        # Целочисленное деление: суммы, не кратные 100, теряют копейки —
        # обратной дороги без потерь у этой миграции нет.
        op.execute(f"UPDATE {table} SET {new} = {new} / 100")
        op.alter_column(table, new, new_column_name=old)
    op.create_check_constraint(_AMOUNT_CHECK, "cash_payment", "amount > 0")
