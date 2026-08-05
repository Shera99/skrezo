"""Статус «пришёл» и причина отмены приёма.

Жизненный цикл приёма разворачивается до полного:
``draft → scheduled → confirmed → arrived → in_progress → completed``
плюс ``cancelled`` (с причиной) и ``no_show``.

* ``arrived`` — пациент пришёл в клинику и ждёт врача. Отличается от
  ``confirmed`` («подтвердил накануне»): регистратор видит очередь в холле;
* ``appointment.cancel_reason`` — текст причины отмены (заполняется вместе со
  статусом ``cancelled``, очищается при снятии отмены);
* CHECK ``ck_appointment_scheduled_at_required`` расширен на ``cancelled``:
  черновик передумавшего пациента закрывают отменой, а даты у него так и не
  появилось. Безопасно — отменённые приёмы и так исключены из EXCLUDE-констрейнта,
  ``busy_spans``, ленты дня и отчётов периода.

Revision ID: 0025_appointment_arrived
Revises: 0024_appointment_draft
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_appointment_arrived"
down_revision: str | None = "0024_appointment_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEDULED_AT_CHECK = "ck_appointment_scheduled_at_required"


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE нельзя использовать в транзакции, где значение
    # создано, а alembic гонит весь upgrade одной транзакцией (см. 0024).
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE appointment_status "
            "ADD VALUE IF NOT EXISTS 'arrived' AFTER 'confirmed'"
        )

    op.add_column("appointment", sa.Column("cancel_reason", sa.Text(), nullable=True))

    # Пустая дата допустима у приёмов, которые так и не состоялись: черновик и
    # отменённый (в т.ч. отменённый прямо из черновика).
    op.drop_constraint(_SCHEDULED_AT_CHECK, "appointment", type_="check")
    op.create_check_constraint(
        _SCHEDULED_AT_CHECK,
        "appointment",
        "scheduled_at IS NOT NULL OR status IN "
        "('draft'::appointment_status, 'cancelled'::appointment_status)",
    )


def downgrade() -> None:
    op.drop_constraint(_SCHEDULED_AT_CHECK, "appointment", type_="check")
    # Отменённые без даты вернуть под прежний CHECK нельзя — это черновики,
    # закрытые отменой; возвращаем их в черновики.
    op.execute(
        "UPDATE appointment SET status = 'draft'::appointment_status "
        "WHERE scheduled_at IS NULL AND status = 'cancelled'::appointment_status"
    )
    op.create_check_constraint(
        _SCHEDULED_AT_CHECK,
        "appointment",
        "scheduled_at IS NOT NULL OR status = 'draft'::appointment_status",
    )

    op.drop_column("appointment", "cancel_reason")
    # Приёмы в статусе 'arrived' возвращаем в 'confirmed': значение enum удалить
    # нельзя (в Postgres нет ALTER TYPE ... DROP VALUE), но данные должны
    # остаться валидными для прежнего набора статусов.
    op.execute(
        "UPDATE appointment SET status = 'confirmed'::appointment_status "
        "WHERE status = 'arrived'::appointment_status"
    )
