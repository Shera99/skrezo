"""Счётчик токенов из кэша префикса (Ф8).

Токены, взятые Gemini из кэша, входят в ``model_input_tokens``, но стоят в
разы дешевле — смета обязана считать их по своей ставке, иначе экономия
от кэша невидима.

Revision ID: 0061_usage_cached_tokens
Revises: 0060_patient_reminders
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_usage_cached_tokens"
down_revision = "0060_patient_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clinic_usage_day",
        sa.Column("model_cached_tokens", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("clinic_usage_day", "model_cached_tokens")
