"""Резюме диалогов и уверенность расшифровки голосовых.

* ``ai_summary`` — выжимка закрытого диалога (§3.3): последние пять по номеру
  подкладываются в контекст нового разговора;
* ``ai_message.transcript_confidence`` — уверенность ASR (§11). Низкая
  уверенность уводит сообщение в эскалацию, а не в догадку: ошибка на входе
  даёт уверенно неправильный ответ на выходе.

Revision ID: 0048_ai_summary
Revises: 0047_slot_hold
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_ai_summary"
down_revision = "0047_slot_hold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_summary",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("ai_conversation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey("patient.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("wa_phone", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_summary_clinic_phone", "ai_summary", ["clinic_id", "wa_phone", "created_at"]
    )
    op.add_column(
        "ai_message", sa.Column("transcript_confidence", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_message", "transcript_confidence")
    op.drop_index("ix_ai_summary_clinic_phone", table_name="ai_summary")
    op.drop_table("ai_summary")
