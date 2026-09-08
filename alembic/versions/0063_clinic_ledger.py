"""Журнал операций и баланс клиники у Skrezo.

Правда о деньгах клиники — в журнале (только добавление): приход (оплата
провайдером, перевод на счёт), расход (списание за подписку, возврат,
корректировка), остаток после каждой операции. Кошелёк хранит текущий
остаток и рубильник автопродления; любое движение — под блокировкой строки
кошелька, чтобы два колбэка не посчитали остаток вразнобой.

``platform_payment`` остаётся записью о попытке оплаты у провайдера:
получает назначение (``purpose``: подписка или пополнение) и сырой ответ на
``init_payment`` — детальные данные сервиса, как и хотел владелец.

Revision ID: 0063_clinic_ledger
Revises: 0062_platform_payments
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_clinic_ledger"
down_revision = "0062_platform_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinic_wallet",
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("balance_tiyin", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("autopay_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("autopay_warned_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recurring_profile_id", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "clinic_ledger_entry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("amount_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="KGS"),
        sa.Column(
            "payment_id",
            sa.Uuid(),
            sa.ForeignKey("platform_payment.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("provider_payload", postgresql.JSONB(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount_tiyin > 0", name="ck_clinic_ledger_entry_amount_positive"),
        sa.CheckConstraint("kind IN ('credit', 'debit')", name="ck_clinic_ledger_entry_kind"),
        sa.UniqueConstraint("idempotency_key", name="uq_clinic_ledger_entry_idempotency"),
    )
    op.create_index(
        "ix_clinic_ledger_entry_clinic", "clinic_ledger_entry", ["clinic_id", "created_at"]
    )
    op.add_column(
        "platform_payment",
        sa.Column("purpose", sa.Text(), nullable=False, server_default="subscription"),
    )
    op.add_column(
        "platform_payment", sa.Column("provider_response", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("platform_payment", "provider_response")
    op.drop_column("platform_payment", "purpose")
    op.drop_index("ix_clinic_ledger_entry_clinic", table_name="clinic_ledger_entry")
    op.drop_table("clinic_ledger_entry")
    op.drop_table("clinic_wallet")
