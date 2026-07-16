"""Клиническое ядро v2: глобальный пациент, одонтограмма, услуги, приёмы.

Заменяет модель 0018 (clinic-scoped patient/visit/tooth_record) на модель платформы:
пациент ГЛОБАЛЬНЫЙ (phone UNIQUE), одонтограмма (patient_tooth, текущее состояние),
услуги клиники (service), приёмы (appointment) и история лечения (appointment_tooth).
Врач = наш membership. Данных в dev нет — таблицы 0018 пересобираются.

Revision ID: 0019_clinical_core_v2
Revises: 0018_patients
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_clinical_core_v2"
down_revision: str | None = "0018_patients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOOTH_STATUS = sa.Enum(
    "healthy", "caries", "filling", "crown", "implant", "missing", "broken", "root",
    "under_treatment", name="tooth_status",
)
_APPT_TYPE = sa.Enum(
    "consultation", "treatment", "operation", "cleaning", "xray", name="appointment_type"
)
_APPT_STATUS = sa.Enum(
    "scheduled", "confirmed", "in_progress", "completed", "cancelled", "no_show",
    name="appointment_status",
)
_APPT_TOOTH_STATUS = sa.Enum(
    "planned", "started", "completed", "cancelled", name="appointment_tooth_status"
)


def upgrade() -> None:
    # 1) Снести модель 0018 (дети → родители).
    op.drop_table("tooth_record")
    op.drop_table("visit")
    op.drop_table("patient")

    # 2) Глобальный пациент.
    op.create_table(
        "patient",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("phone", sa.Text(), nullable=False, unique=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("middle_name", sa.Text(), nullable=True),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("avatar", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # 3) Одонтограмма (текущее состояние зуба).
    op.create_table(
        "patient_tooth",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "patient_id", sa.Uuid(), sa.ForeignKey("patient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tooth_number", sa.SmallInteger(), nullable=False),
        sa.Column("status", _TOOTH_STATUS, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("patient_id", "tooth_number", name="uq_patient_tooth"),
    )
    op.create_index("ix_patient_tooth_patient_id", "patient_tooth", ["patient_id"])

    # 4) Услуги клиники (прайс).
    op.create_table(
        "service",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_service_clinic_id", "service", ["clinic_id"])

    # 5) Приёмы.
    op.create_table(
        "appointment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id", sa.Uuid(), sa.ForeignKey("patient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doctor_membership_id", sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("appointment_type", _APPT_TYPE, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("status", _APPT_STATUS, nullable=False),
        sa.Column("price_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_appointment_clinic_id", "appointment", ["clinic_id"])
    op.create_index("ix_appointment_patient_id", "appointment", ["patient_id"])
    op.create_index("ix_appointment_scheduled_at", "appointment", ["scheduled_at"])

    # 6) История лечения (запись = зуб в приёме).
    op.create_table(
        "appointment_tooth",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "appointment_id", sa.Uuid(),
            sa.ForeignKey("appointment.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "patient_tooth_id", sa.Uuid(),
            sa.ForeignKey("patient_tooth.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "service_id", sa.Uuid(), sa.ForeignKey("service.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "doctor_membership_id", sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("status", _APPT_TOOTH_STATUS, nullable=False),
        sa.Column("price", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_appointment_tooth_appointment_id", "appointment_tooth", ["appointment_id"])
    op.create_index(
        "ix_appointment_tooth_patient_tooth_id", "appointment_tooth", ["patient_tooth_id"]
    )


def downgrade() -> None:
    op.drop_table("appointment_tooth")
    op.drop_table("appointment")
    op.drop_table("service")
    op.drop_table("patient_tooth")
    op.drop_table("patient")
    for enum in (_APPT_TOOTH_STATUS, _APPT_STATUS, _APPT_TYPE, _TOOTH_STATUS):
        enum.drop(op.get_bind(), checkfirst=True)

    # Восстановить упрощённые таблицы 0018 (структурно).
    op.create_table(
        "patient",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE")),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
