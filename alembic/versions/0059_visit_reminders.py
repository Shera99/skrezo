"""Подтверждение визита из напоминания (Ф7, инкремент 4).

Напоминание уходит шаблоном WhatsApp с кнопками «Приду» / «Перенести» за
сутки до приёма; нажатие «Приду» переводит приём в ``confirmed`` той же
механикой кнопки, что и запись. Строка на приём хранит, что напоминание
ушло (одно на приём — уникальность), и чем ответил пациент; без ответа за
несколько часов до визита — событие администратору.

Revision ID: 0059_visit_reminders
Revises: 0058_ai_skills
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_visit_reminders"
down_revision = "0058_ai_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "appointment_reminder",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.UniqueConstraint("appointment_id", name="uq_appointment_reminder_appointment"),
    )
    op.create_index(
        "ix_appointment_reminder_clinic_status",
        "appointment_reminder",
        ["clinic_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_appointment_reminder_clinic_status", table_name="appointment_reminder")
    op.drop_table("appointment_reminder")
