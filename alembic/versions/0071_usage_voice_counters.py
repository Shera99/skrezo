"""Счётчики голосового помощника приложения в дневном учёте.

Голосовой помощник (``POST /clinic/llm/generate``) ходит в ту же модель, что
ассистент пациентов, но это другой контур и другая цена вопроса для клиники:
его обращения и токены считаются своими колонками, а не подмешиваются к
ассистенту. Смета считает их по тем же ставкам модели.

Revision ID: 0071_usage_voice_counters
Revises: 0070_leads_into_applications
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0071_usage_voice_counters"
down_revision = "0070_leads_into_applications"
branch_labels = None
depends_on = None

_COLUMNS = ("voice_calls", "voice_input_tokens", "voice_output_tokens")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "clinic_usage_day",
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("clinic_usage_day", name)
