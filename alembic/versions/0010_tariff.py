"""Версии тарифа + ступени.

Revision ID: 0010_tariff
Revises: 0009_subscription
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_tariff"
down_revision: str | None = "0009_subscription"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Переиспользуем существующий enum clinic_type (создан в 0001) — не создаём заново.
_clinic_type = postgresql.ENUM("solo", "clinic", name="clinic_type", create_type=False)


def upgrade() -> None:
    op.create_table(
        "tariff_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("type", _clinic_type, nullable=False),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["platform_user.id"], name="fk_tariff_version_created_by"
        ),
    )
    op.create_table(
        "tariff_tier",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tariff_version_id", sa.Uuid(), nullable=False),
        sa.Column("seats_from", sa.Integer(), nullable=False),
        sa.Column("seats_to", sa.Integer(), nullable=True),
        sa.Column("price_per_seat", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tariff_version_id"],
            ["tariff_version.id"],
            name="fk_tariff_tier_version",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("tariff_tier")
    op.drop_table("tariff_version")
