"""Индекс под частичные возвраты: «сколько возвращено по платежу».

Возврат стал частичным и многократным, поэтому остаток к возврату считается
агрегатом ``SUM(amount_tiyin) GROUP BY refunds_payment_id`` — и он же
подмешивается в журнал кассы к каждой строке. Без индекса на
``refunds_payment_id`` это seq scan по всей кассе клиники на каждой странице
журнала.

Частичный (``WHERE refunds_payment_id IS NOT NULL``): ссылка есть только у
возвратов, а их в кассе меньшинство — незачем держать в индексе все платежи.

Revision ID: 0028_refunds_index
Revises: 0027_no_charge_note_force
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_refunds_index"
down_revision: str | None = "0027_no_charge_note_force"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_cash_payment_refunds_payment"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "cash_payment",
        ["refunds_payment_id"],
        postgresql_where="refunds_payment_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="cash_payment")
