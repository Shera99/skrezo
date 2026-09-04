"""Кабинет ассистента (Ф5): непрочитанное, черновики, уверенность, длительность медиа.

* ``ai_conversation.read_message_id`` — общая для клиники отметка прочтения:
  непрочитанное = сообщения пациента новее её;
* ``ai_message.confidence`` — уверенность модели («уверенный ответ» /
  «проверьте текст»), раньше терялась;
* ``ai_message.media_duration_sec`` — длительность голосового (Meta её в
  вебхуке не присылает, читаем из файла);
* ``ai_message.review`` / ``reviewed_by`` / ``reviewed_at`` / ``original_body``
  — жизненный цикл черновика в режиме «Проверяю»: pending → approved |
  edited | discarded | superseded; исходный текст хранится ради обучения;
* ``pg_trgm`` + GIN по тексту сообщений — поиск по переписке без
  полнотекстового движка.

Revision ID: 0050_assistant_cabinet
Revises: 0049_patient_source
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_assistant_cabinet"
down_revision = "0049_patient_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_conversation", sa.Column("read_message_id", sa.BigInteger(), nullable=True))
    op.add_column("ai_message", sa.Column("confidence", sa.Text(), nullable=True))
    op.add_column("ai_message", sa.Column("media_duration_sec", sa.Integer(), nullable=True))
    op.add_column("ai_message", sa.Column("review", sa.Text(), nullable=True))
    op.add_column(
        "ai_message",
        sa.Column(
            "reviewed_by",
            sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("ai_message", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_message", sa.Column("original_body", sa.Text(), nullable=True))
    # Черновики на решении ищутся по диалогу: partial-индекс держит их наперечёт.
    op.create_index(
        "ix_ai_message_pending_review",
        "ai_message",
        ["conversation_id"],
        postgresql_where=sa.text("review = 'pending'"),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_ai_message_body_trgm ON ai_message USING gin (body gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_message_body_trgm")
    op.drop_index("ix_ai_message_pending_review", table_name="ai_message")
    op.drop_column("ai_message", "original_body")
    op.drop_column("ai_message", "reviewed_at")
    op.drop_column("ai_message", "reviewed_by")
    op.drop_column("ai_message", "review")
    op.drop_column("ai_message", "media_duration_sec")
    op.drop_column("ai_message", "confidence")
    op.drop_column("ai_conversation", "read_message_id")
