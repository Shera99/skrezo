"""Таблица аудита платформенной зоны.

Revision ID: 0014_audit_log
Revises: 0013_mgr_staff
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_audit_log"
down_revision: str | None = "0013_mgr_staff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["platform_user.id"], name="fk_audit_log_actor"),
    )
    # Индекс под курсорную пагинацию (свежие сверху).
    op.create_index("ix_audit_log_created_at_id", "audit_log", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at_id", table_name="audit_log")
    op.drop_table("audit_log")
