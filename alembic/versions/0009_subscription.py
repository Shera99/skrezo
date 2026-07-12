"""Подписка клиники + лог событий статуса.

Revision ID: 0009_subscription
Revises: 0008_lead
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_subscription"
down_revision: str | None = "0008_lead"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_status = postgresql.ENUM(
    "trial",
    "awaiting_card",
    "active",
    "past_due",
    "blocked",
    "cancelled",
    name="subscription_status",
    create_type=False,
)


def upgrade() -> None:
    _status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "subscription",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("status", _status, nullable=False),
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("card_attach_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("card_attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id"], ["clinic.id"], name="fk_subscription_clinic", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("clinic_id", name="uq_subscription_clinic"),
    )

    op.create_table(
        "subscription_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", _status, nullable=True),
        sa.Column("to_status", _status, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscription.id"],
            name="fk_subscription_event_subscription",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("subscription_event")
    op.drop_table("subscription")
    postgresql.ENUM(name="subscription_status").drop(op.get_bind(), checkfirst=True)
