"""Индекс под метрики обучения (Ф6, инкремент 5).

Метрики считают ответы бота клиники за период: без индекса по
(клиника, роль, время) это скан всей ``ai_message`` — при десятках тысяч
сообщений в сутки платформы дашборд стал бы минутным.

Revision ID: 0055_ai_metrics_index
Revises: 0054_ai_regression
"""

from __future__ import annotations

from alembic import op

revision = "0055_ai_metrics_index"
down_revision = "0054_ai_regression"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ai_message_clinic_role_created",
        "ai_message",
        ["clinic_id", "role", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_message_clinic_role_created", table_name="ai_message")
