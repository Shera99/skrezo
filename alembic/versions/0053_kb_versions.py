"""Версии документов базы знаний (Ф6, инкремент 3 — редактор и откат).

Знание вступает в силу сразу, но каждое изменение — точка, к которой можно
вернуться: после каждого сохранения пишется снимок документа. Последняя
версия всегда равна текущему состоянию; откат — копия старой версии в
документ плюс новая версия с пометкой «откат к N» (история не переписывается).

Revision ID: 0053_kb_versions
Revises: 0052_knowledge_gaps
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_kb_versions"
down_revision = "0052_knowledge_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kb_document",
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "kb_document_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("kb_document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "actor_id",
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
        sa.UniqueConstraint("document_id", "version_no", name="uq_kb_document_version_no"),
    )
    op.create_index(
        "ix_kb_document_version_clinic", "kb_document_version", ["clinic_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_kb_document_version_clinic", table_name="kb_document_version")
    op.drop_table("kb_document_version")
    op.drop_column("kb_document", "version_no")
