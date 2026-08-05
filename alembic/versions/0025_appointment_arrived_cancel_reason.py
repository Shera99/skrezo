"""Статус «пришёл», причина отмены справочником и право на откат статуса.

Жизненный цикл приёма разворачивается до полного:
``draft → scheduled → confirmed → arrived → in_progress → completed``
плюс ``cancelled`` (с причиной) и ``no_show``.

* ``arrived`` — пациент пришёл в клинику и ждёт врача. Отличается от
  ``confirmed`` («подтвердил накануне»): регистратор видит очередь в холле;
* ``appointment.cancel_reason`` — причина отмены **справочником** (свободный
  текст нечем считать, а «Пульс дня» с причинами отмен — витрина продукта) и
  ``cancel_comment`` — пояснение (обязательно при ``other``);
* право ``appointments.status_rollback`` — откат статуса назад по воронке;
  выдаётся менеджеру (владелец получает все права по признаку ``is_owner``);
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
_ROLLBACK_PERMISSION = "appointments.status_rollback"
_CANCEL_REASON = sa.Enum(
    "patient_request",
    "patient_illness",
    "clinic_reschedule",
    "doctor_unavailable",
    "duplicate",
    "other",
    name="cancel_reason",
)


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE нельзя использовать в транзакции, где значение
    # создано, а alembic гонит весь upgrade одной транзакцией (см. 0024).
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE appointment_status "
            "ADD VALUE IF NOT EXISTS 'arrived' AFTER 'confirmed'"
        )

    _CANCEL_REASON.create(op.get_bind(), checkfirst=True)
    op.add_column("appointment", sa.Column("cancel_reason", _CANCEL_REASON, nullable=True))
    op.add_column("appointment", sa.Column("cancel_comment", sa.Text(), nullable=True))

    # Пустая дата допустима у приёмов, которые так и не состоялись: черновик и
    # отменённый (в т.ч. отменённый прямо из черновика).
    op.drop_constraint(_SCHEDULED_AT_CHECK, "appointment", type_="check")
    op.create_check_constraint(
        _SCHEDULED_AT_CHECK,
        "appointment",
        "scheduled_at IS NOT NULL OR status IN "
        "('draft'::appointment_status, 'cancelled'::appointment_status)",
    )

    # Право на откат: на чистой БД его уже засеял 0004 из каталога, на живой —
    # вставляем здесь. Обе вставки идемпотентны.
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_ROLLBACK_PERMISSION}', 'appointments',
               'Откат статуса приёма назад',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_ROLLBACK_PERMISSION}')
        """
    )
    # Менеджеру — и в глобальных шаблонах ролей, и во всех клиниках.
    op.execute(
        f"""
        INSERT INTO role_permission (role_id, permission_id)
        SELECT r.id, p.id
        FROM role r, permission p
        WHERE r.key = 'manager' AND p.key = '{_ROLLBACK_PERMISSION}'
          AND NOT EXISTS (
              SELECT 1 FROM role_permission rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM role_permission rp
        USING permission p
        WHERE rp.permission_id = p.id AND p.key = '{_ROLLBACK_PERMISSION}'
        """
    )
    op.execute(f"DELETE FROM permission WHERE key = '{_ROLLBACK_PERMISSION}'")

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

    op.drop_column("appointment", "cancel_comment")
    op.drop_column("appointment", "cancel_reason")
    _CANCEL_REASON.drop(op.get_bind(), checkfirst=True)

    # Приёмы в статусе 'arrived' возвращаем в 'confirmed': значение enum удалить
    # нельзя (в Postgres нет ALTER TYPE ... DROP VALUE), но данные должны
    # остаться валидными для прежнего набора статусов.
    op.execute(
        "UPDATE appointment SET status = 'confirmed'::appointment_status "
        "WHERE status = 'arrived'::appointment_status"
    )
