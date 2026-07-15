"""Клиническое ядро (первая итерация): patient, visit, tooth_record.

Пациенты клиники, визиты (приёмы) и записи по зубам (FDI). Источник правды по
клиническим данным — этот сервис.

Revision ID: 0018_patients
Revises: 0017_unify_auth
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_patients"
down_revision: str | None = "0017_unify_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patient",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_patient_clinic_id", "patient", ["clinic_id"])

    op.create_table(
        "visit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey("patient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doctor_membership_id",
            sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visited_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("complaint", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_visit_clinic_id", "visit", ["clinic_id"])
    op.create_index("ix_visit_patient_id", "visit", ["patient_id"])

    op.create_table(
        "tooth_record",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey("patient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visit_id",
            sa.Uuid(),
            sa.ForeignKey("visit.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tooth", sa.SmallInteger(), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=True),
        sa.Column("treatment", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_tooth_record_clinic_id", "tooth_record", ["clinic_id"])
    op.create_index("ix_tooth_record_patient_id", "tooth_record", ["patient_id"])
    op.create_index("ix_tooth_record_visit_id", "tooth_record", ["visit_id"])
    op.create_index(
        "ix_tooth_record_patient_tooth", "tooth_record", ["patient_id", "tooth"]
    )


def downgrade() -> None:
    op.drop_table("tooth_record")
    op.drop_table("visit")
    op.drop_table("patient")
