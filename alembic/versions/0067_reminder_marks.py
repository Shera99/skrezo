"""Отметки «напомнили» — журнал связи сотрудника с пациентом по поводу.

Отметки жили в браузере: второй администратор не видел, кому уже звонили.
Таблица хранит факт напоминания — пациент, повод, приём-источник, кто и
когда. Это не рассылка: автоматические напоминания ассистента остаются в
``patient_reminder`` и ``appointment_reminder``.

Revision ID: 0067_reminder_marks
Revises: 0066_whatsapp_template_labels
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_reminder_marks"
down_revision = "0066_whatsapp_template_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminder_mark",
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
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "appointment_id",
            sa.Uuid(),
            sa.ForeignKey("appointment.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "marked_by_membership_id",
            sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_reminder_mark_clinic_patient",
        "reminder_mark",
        ["clinic_id", "patient_id", "marked_at"],
    )
    op.create_index("ix_reminder_mark_clinic_marked", "reminder_mark", ["clinic_id", "marked_at"])


def downgrade() -> None:
    op.drop_index("ix_reminder_mark_clinic_marked", table_name="reminder_mark")
    op.drop_index("ix_reminder_mark_clinic_patient", table_name="reminder_mark")
    op.drop_table("reminder_mark")
