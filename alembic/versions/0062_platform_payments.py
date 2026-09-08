"""Платежи клиник за подписку через FreedomPay (платёжная страница).

Провайдер пока даёт только переход на свою страницу оплаты (автосписание —
через 2–3 месяца), поэтому платёж — явное действие владельца: строка
создаётся до перехода, подтверждается серверным колбэком с подписью, и
только он открывает оплаченный период. Профиль автосписания
(``recurring_profile_id``) — задел на потом.

Revision ID: 0062_platform_payments
Revises: 0061_usage_cached_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062_platform_payments"
down_revision = "0061_usage_cached_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_payment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False, server_default="freedompay"),
        sa.Column("provider_payment_id", sa.Text(), nullable=True),
        sa.Column("recurring_profile_id", sa.Text(), nullable=True),
        sa.Column("amount_tiyin", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="KGS"),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("redirect_url", sa.Text(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("failure_description", sa.Text(), nullable=True),
        sa.Column("callback", postgresql.JSONB(), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_platform_payment_clinic", "platform_payment", ["clinic_id", "created_at"])
    op.create_index("ix_platform_payment_status", "platform_payment", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_platform_payment_status", table_name="platform_payment")
    op.drop_index("ix_platform_payment_clinic", table_name="platform_payment")
    op.drop_table("platform_payment")
