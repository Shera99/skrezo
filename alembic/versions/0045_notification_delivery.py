"""Телеметрия доставки уведомлений (Ф3, инкремент 7).

Строка — попытка доставки записи инбокса на устройство: отправка, ошибка
провайдера, подтверждение показа приложением. По ней считается реальная
доставляемость по платформам (критерий готовности Ф3 №6).

Revision ID: 0045_notification_delivery
Revises: 0044_push_job
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_notification_delivery"
down_revision: str | None = "0044_push_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("inbox_item_id", sa.BigInteger(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("displayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["inbox_item_id"], ["inbox_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_delivery_inbox", "notification_delivery", ["inbox_item_id"]
    )
    op.create_index(
        "ix_notification_delivery_created", "notification_delivery", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_created", table_name="notification_delivery")
    op.drop_index("ix_notification_delivery_inbox", table_name="notification_delivery")
    op.drop_table("notification_delivery")
