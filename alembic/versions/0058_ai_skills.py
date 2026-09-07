"""Матрица умений ассистента (Ф7, инкремент 3).

Один enum режима заменяется парой «лестница + потолки умений»: ``ai_mode``
остаётся общей ступенью доверия, а ``ai_skills`` хранит потолок по каждому
умению (booking — запись, changes — перенос и отмена): off | assisted | auto.
Действующий уровень умения = min(ступень, потолок). Пустой объект — потолков
нет, умения следуют за ступенью.

Revision ID: 0058_ai_skills
Revises: 0057_patient_reschedule
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_ai_skills"
down_revision = "0057_patient_reschedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clinic",
        sa.Column(
            "ai_skills",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("clinic", "ai_skills")
