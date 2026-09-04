"""Пробелы знаний ассистента (Ф6, инкремент 1) — очередь экрана «Обучение».

Вопрос, на который бот не ответил, — событие, а не потеря: строка с дословной
формулировкой, нормализованной формой для склейки, счётчиком «спросили ×N» и
ссылками на диалоги. Похожие формулировки склеиваются по триграммам
(``pg_trgm`` уже включён миграцией 0050). Ответ администратора рождает
документ базы знаний — ссылка ``kb_document_id`` хранит, чему бот научился.

Revision ID: 0052_knowledge_gaps
Revises: 0051_usage_metering
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_knowledge_gaps"
down_revision = "0051_usage_metering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_knowledge_gap",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column("ask_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "conversation_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "kb_document_id",
            sa.Uuid(),
            sa.ForeignKey("kb_document.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolved_by",
            sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_knowledge_gap_clinic_status",
        "ai_knowledge_gap",
        ["clinic_id", "status", "last_asked_at"],
    )
    op.execute(
        "CREATE INDEX ix_ai_knowledge_gap_normalized_trgm ON ai_knowledge_gap "
        "USING gin (normalized gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_knowledge_gap_normalized_trgm")
    op.drop_index("ix_ai_knowledge_gap_clinic_status", table_name="ai_knowledge_gap")
    op.drop_table("ai_knowledge_gap")
