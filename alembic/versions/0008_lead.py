"""Таблица лидов (заявка «Больше одного»).

Revision ID: 0008_lead
Revises: 0007_clinic_city
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_lead"
down_revision: str | None = "0007_clinic_city"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False, unique=True),
        sa.Column("clinic_name", sa.Text(), nullable=False),
        sa.Column("doctors_count_range", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("converted_clinic_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["converted_clinic_id"], ["clinic.id"], name="fk_lead_converted_clinic"
        ),
    )


def downgrade() -> None:
    op.drop_table("lead")
