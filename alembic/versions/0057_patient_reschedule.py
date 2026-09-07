"""Перенос и отмена записи пациентом через ассистента (Ф7, инкремент 2).

Резерв слота получает вид (``kind``: book | reschedule | cancel) и ссылку на
изменяемую запись — механика кнопки та же, что у записи: модель только
предлагает, действие выполняет обработчик кнопки штатным use-case правки
приёма (те же проверки занятости, события, аудит).

Аудит: действие по кнопке пациента совершает не сотрудник — ``actor_id``
становится необязательным; такие записи несут ``payload.via = "assistant"``.

Revision ID: 0057_patient_reschedule
Revises: 0056_trust_ladder
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057_patient_reschedule"
down_revision = "0056_trust_ladder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("slot_hold", sa.Column("kind", sa.Text(), nullable=False, server_default="book"))
    op.add_column(
        "slot_hold",
        sa.Column(
            "target_appointment_id",
            sa.Uuid(),
            sa.ForeignKey("appointment.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.alter_column("audit_log", "actor_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM audit_log WHERE actor_id IS NULL")
    op.alter_column("audit_log", "actor_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("slot_hold", "target_appointment_id")
    op.drop_column("slot_hold", "kind")
