"""Касса клиники: леджер платежей и возвратов по приёмам.

Добавляет:

* enum-типы ``payment_method`` (нал/карта/перевод) и ``payment_kind``
  (платёж/возврат);
* таблицу ``cash_payment`` — кассовые операции клиники по приёмам. Долг приёма
  считается как ``appointment.price_total − Σплатежей + Σвозвратов``. Это касса
  КЛИНИКИ (оплата приёмов), не путать с ``platform_payment`` (биллинг Skrezo).

Индексы: журнал по клинике+времени, свой журнал кассира (RBAC ``cash.view_own``)
и по приёму — для расчёта остатка.

Revision ID: 0021_cash
Revises: 0020_schedule
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_cash"
down_revision: str | None = "0020_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAYMENT_METHOD = sa.Enum("cash", "card", "transfer", name="payment_method")
_PAYMENT_KIND = sa.Enum("payment", "refund", name="payment_kind")


def upgrade() -> None:
    op.create_table(
        "cash_payment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "appointment_id", sa.Uuid(), sa.ForeignKey("appointment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id", sa.Uuid(), sa.ForeignKey("patient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("method", _PAYMENT_METHOD, nullable=False),
        sa.Column("kind", _PAYMENT_KIND, nullable=False, server_default="payment"),
        sa.Column(
            "recorded_by_membership_id", sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "refunds_payment_id", sa.Uuid(),
            sa.ForeignKey("cash_payment.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_cash_payment_amount_positive"),
    )
    op.create_index("ix_cash_payment_patient_id", "cash_payment", ["patient_id"])
    op.create_index("ix_cash_payment_clinic_created", "cash_payment", ["clinic_id", "created_at"])
    op.create_index(
        "ix_cash_payment_recorded_by",
        "cash_payment",
        ["clinic_id", "recorded_by_membership_id", "created_at"],
    )
    op.create_index("ix_cash_payment_appointment", "cash_payment", ["appointment_id"])


def downgrade() -> None:
    op.drop_index("ix_cash_payment_appointment", table_name="cash_payment")
    op.drop_index("ix_cash_payment_recorded_by", table_name="cash_payment")
    op.drop_index("ix_cash_payment_clinic_created", table_name="cash_payment")
    op.drop_index("ix_cash_payment_patient_id", table_name="cash_payment")
    op.drop_table("cash_payment")
    bind = op.get_bind()
    _PAYMENT_KIND.drop(bind, checkfirst=True)
    _PAYMENT_METHOD.drop(bind, checkfirst=True)
