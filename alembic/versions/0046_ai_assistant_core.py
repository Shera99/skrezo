"""Каркас AI-ассистента (Ф2, инкремент 1).

* ``clinic.ai_mode`` — off/shadow/assisted/auto (новые клиники — off);
* ``patient.preferred_lang`` — язык общения с ассистентом;
* ``glossary_term`` — словарь фраз с ОЧЕРЕДЬЮ предложений (pending → approved:
  бот переспрашивает пациента, админ одобряет — автозаписи в промпт нет);
* ``kb_document`` — база знаний клиники (текст целиком в промпт, без векторов);
* ``ai_tool_call`` — журнал инструментов (по нему валидатор сверяет факты);
* ``ai_escalation`` — карточки «бот остановился, нужен человек».

Revision ID: 0046_ai_assistant_core
Revises: 0045_notification_delivery
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_ai_assistant_core"
down_revision: str | None = "0045_notification_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_lang_enum = postgresql.ENUM("kg", "kz", "ru", "en", name="lang_enum", create_type=False)


def upgrade() -> None:
    ai_mode = sa.Enum("off", "shadow", "assisted", "auto", name="ai_mode")
    ai_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "clinic",
        sa.Column("ai_mode", ai_mode, nullable=False, server_default="off"),
    )
    op.add_column("patient", sa.Column("preferred_lang", _lang_enum, nullable=True))

    op.create_table(
        "glossary_term",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=True),
        sa.Column("phrase", sa.Text(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", name="glossary_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_glossary_term_clinic_status", "glossary_term", ["clinic_id", "status"])

    op.create_table(
        "kb_document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_document_clinic_lang", "kb_document", ["clinic_id", "lang"])

    op.create_table(
        "ai_tool_call",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["ai_message.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_tool_call_conversation", "ai_tool_call", ["conversation_id", "id"])

    op.create_table(
        "ai_escalation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_escalation_clinic_open",
        "ai_escalation",
        ["clinic_id", "resolved_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_escalation_clinic_open", table_name="ai_escalation")
    op.drop_table("ai_escalation")
    op.drop_index("ix_ai_tool_call_conversation", table_name="ai_tool_call")
    op.drop_table("ai_tool_call")
    op.drop_index("ix_kb_document_clinic_lang", table_name="kb_document")
    op.drop_table("kb_document")
    op.drop_index("ix_glossary_term_clinic_status", table_name="glossary_term")
    op.drop_table("glossary_term")
    op.drop_column("patient", "preferred_lang")
    op.drop_column("clinic", "ai_mode")
    sa.Enum(name="glossary_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ai_mode").drop(op.get_bind(), checkfirst=True)
    # lang_enum нам не принадлежит (создан в 0006_i18n) — не трогаем.
