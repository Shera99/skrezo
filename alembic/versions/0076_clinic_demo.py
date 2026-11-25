"""Демо-клиника: признак у клиники и состояние сброса.

``clinic.demo`` включает команда Skrezo. ``clinic_demo`` хранит границу, раньше
которой сброс данные не трогает, и списки собранного им демо-дня. Обе правки —
только добавление: старый код о них не знает и продолжает работать, порядок
«миграция, потом перезапуск» безопасен.

Revision ID: 0076_clinic_demo
Revises: 0075_patient_file_teeth_ct
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0076_clinic_demo"
down_revision = "0075_patient_file_teeth_ct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clinic",
        sa.Column("demo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "clinic_demo",
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "appointment_ids",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "patient_ids",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_table("clinic_demo")
    op.drop_column("clinic", "demo")
