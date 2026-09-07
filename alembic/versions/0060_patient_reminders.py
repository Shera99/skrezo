"""Напоминания о долге и повторном осмотре (Ф7, инкремент 5).

Исходящие шаблоны WhatsApp по расписанию: долг по завершённому приёму
(касса) и приглашение на повторный осмотр (история приёмов). Одно
напоминание на источник — (вид, приём) уникальны; ответ по кнопке
повторного осмотра хранится тут же.

Revision ID: 0060_patient_reminders
Revises: 0059_visit_reminders
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060_patient_reminders"
down_revision = "0059_visit_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_reminder",
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
            sa.ForeignKey("appointment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("ai_conversation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("wa_message_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="sent"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("kind", "appointment_id", name="uq_patient_reminder_source"),
    )
    op.create_index(
        "ix_patient_reminder_clinic_kind",
        "patient_reminder",
        ["clinic_id", "kind", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_patient_reminder_clinic_kind", table_name="patient_reminder")
    op.drop_table("patient_reminder")
