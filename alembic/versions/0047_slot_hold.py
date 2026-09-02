"""Мягкий резерв слота для записи через ассистента (Ф2, §6.4).

Revision ID: 0047_slot_hold
Revises: 0046_ai_assistant_core
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_slot_hold"
down_revision: str | None = "0046_ai_assistant_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slot_hold",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["doctor_membership_id"], ["membership.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["service_id"], ["service.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointment.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_slot_hold_doctor_time", "slot_hold", ["doctor_membership_id", "starts_at"])
    op.create_index("ix_slot_hold_expires", "slot_hold", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_slot_hold_expires", table_name="slot_hold")
    op.drop_index("ix_slot_hold_doctor_time", table_name="slot_hold")
    op.drop_table("slot_hold")
