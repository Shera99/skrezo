"""Отложенные пуши (Ф3, инкремент 5): push_job с отменой по показу.

``normal``-приоритет уходит пушем через 10 секунд, если приложение не
подтвердило показ записи инбокса (``displayed``). Строки переживают рестарт
воркера; двойную отправку исключает атомарный захват по ``sent_at``/
``canceled_at``.

Revision ID: 0044_push_job
Revises: 0043_events_inbox
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_push_job"
down_revision: str | None = "0043_events_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_job",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("inbox_item_id", sa.BigInteger(), nullable=False),
        sa.Column("send_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["inbox_item_id"], ["inbox_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_push_job_due",
        "push_job",
        ["send_after"],
        postgresql_where=sa.text("sent_at IS NULL AND canceled_at IS NULL"),
    )
    op.create_index("ix_push_job_inbox_item", "push_job", ["inbox_item_id"])


def downgrade() -> None:
    op.drop_index("ix_push_job_inbox_item", table_name="push_job")
    op.drop_index("ix_push_job_due", table_name="push_job")
    op.drop_table("push_job")
