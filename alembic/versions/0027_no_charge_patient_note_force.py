"""Бесплатный приём, заметка пациента и право на запись вне графика.

Три решения владельца одной миграцией — все три добавляют по полю или праву и
ничего не переносят:

* ``appointment.no_charge`` + ``no_charge_comment`` — приём без позиций закрыть
  нельзя, кроме явного «бесплатно» с пояснением. CHECK
  ``ck_appointment_no_charge_comment`` стережёт пару: флаг без пояснения — это
  снова нулевой чек, только с галочкой;
* ``patient.note`` — заметка о пациенте (аллергии, особенности приёма). Пациент
  глобальный, и заметка живёт вместе с ним — как и сквозная история зуба;
* право ``appointments.force_booking`` — запись вне рабочих часов врача. Клиники
  принимают вне графика постоянно, и жёсткий запрет приводит лишь к тому, что
  приём не заведут в системе. Пересечение приёмов и отпуск врача правом не
  обходятся — это проверяется отдельно и всегда.

Revision ID: 0027_no_charge_note_force
Revises: 0026_money_in_tiyin
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_no_charge_note_force"
down_revision: str | None = "0026_money_in_tiyin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NO_CHARGE_CHECK = "ck_appointment_no_charge_comment"
_FORCE_PERMISSION = "appointments.force_booking"
_FORCE_DESCRIPTION = "Запись вне графика врача"


def upgrade() -> None:
    op.add_column(
        "appointment",
        sa.Column(
            "no_charge", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("appointment", sa.Column("no_charge_comment", sa.Text(), nullable=True))
    op.create_check_constraint(
        _NO_CHARGE_CHECK,
        "appointment",
        "NOT no_charge OR no_charge_comment IS NOT NULL",
    )

    op.add_column("patient", sa.Column("note", sa.Text(), nullable=True))

    # Право на запись вне графика: на чистой БД его уже засеял 0004 из каталога,
    # на живой — вставляем здесь. Обе вставки идемпотентны.
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_FORCE_PERMISSION}', 'appointments',
               '{_FORCE_DESCRIPTION}',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_FORCE_PERMISSION}')
        """
    )
    # Менеджеру — и в глобальных шаблонах ролей, и во всех клиниках. Врачу не
    # выдаём: приём вне графика — решение администратора.
    op.execute(
        f"""
        INSERT INTO role_permission (role_id, permission_id)
        SELECT r.id, p.id
        FROM role r, permission p
        WHERE r.key = 'manager' AND p.key = '{_FORCE_PERMISSION}'
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
        WHERE rp.permission_id = p.id AND p.key = '{_FORCE_PERMISSION}'
        """
    )
    op.execute(f"DELETE FROM permission WHERE key = '{_FORCE_PERMISSION}'")

    op.drop_column("patient", "note")

    op.drop_constraint(_NO_CHARGE_CHECK, "appointment", type_="check")
    op.drop_column("appointment", "no_charge_comment")
    op.drop_column("appointment", "no_charge")
