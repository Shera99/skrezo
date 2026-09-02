"""Происхождение карточки пациента (§6.5 ТЗ ассистента).

Карточки, заведённые ботом со слов пациента, администратор просматривает
отдельно: имя записано дословно и не проверено документом.

Revision ID: 0049_patient_source
Revises: 0048_ai_summary
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_patient_source"
down_revision = "0048_ai_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "patient",
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("patient", "source")
