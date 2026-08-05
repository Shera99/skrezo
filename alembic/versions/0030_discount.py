"""Скидка: право ``cash.discount``, потолок в роли и скидка на приёме.

Скидка — единственная операция, где сотрудник в одиночку уменьшает выручку,
поэтому «можно» и «сколько» разведены:

* право ``cash.discount`` — разрешает операцию вообще (по умолчанию у владельца
  и администратора);
* ``role.max_discount_percent`` — потолок роли: владелец 100, администратор 20,
  врач 0. Значения берутся из каталога ролей (единый источник истины);
* ``appointment.discount_percent`` + ``discount_tiyin`` — скидка приёма.
  ``price_total_tiyin`` остаётся итогом **к оплате** (сумма позиций минус
  скидка), поэтому касса и долги продолжают считать по нему без изменений.

Проценты, а не суммы: потолок роли задан в процентах, и только так его можно
проверить до расчёта. Сумма скидки хранится рядом — чтобы чек показывал её как
факт, а отчёт мог сложить, сколько клиника раздала за период.

Revision ID: 0030_discount
Revises: 0029_appointment_items
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.access.domain.permissions import DEFAULT_ROLES

revision: str = "0030_discount"
down_revision: str | None = "0029_appointment_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISCOUNT_PERMISSION = "cash.discount"
_DISCOUNT_DESCRIPTION = "Скидка на приём (в пределах потолка роли)"
_ROLE_CHECK = "ck_role_max_discount_percent"
_APPOINTMENT_CHECK = "ck_appointment_discount_percent"


def upgrade() -> None:
    op.add_column(
        "role",
        sa.Column(
            "max_discount_percent", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_check_constraint(
        _ROLE_CHECK, "role", "max_discount_percent BETWEEN 0 AND 100"
    )
    # Потолки системных ролей — из каталога (0004 сеет роли до появления колонки,
    # поэтому проставляются здесь; правило одно и для шаблонов, и для клиник).
    for role in DEFAULT_ROLES:
        op.execute(
            f"UPDATE role SET max_discount_percent = {role.max_discount_percent} "
            f"WHERE key = '{role.key}'"
        )
    # Владелец: страховка на случай ролей без key (клонированных вручную).
    op.execute("UPDATE role SET max_discount_percent = 100 WHERE is_owner")

    op.add_column(
        "appointment",
        sa.Column(
            "discount_percent", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "appointment",
        sa.Column("discount_tiyin", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        _APPOINTMENT_CHECK, "appointment", "discount_percent BETWEEN 0 AND 100"
    )

    # Право на скидку: на чистой БД его засеял 0004 из каталога, на живой —
    # вставляем здесь. Обе вставки идемпотентны.
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_DISCOUNT_PERMISSION}', 'cash',
               '{_DISCOUNT_DESCRIPTION}',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_DISCOUNT_PERMISSION}')
        """
    )
    op.execute(
        f"""
        INSERT INTO role_permission (role_id, permission_id)
        SELECT r.id, p.id
        FROM role r, permission p
        WHERE r.key = 'manager' AND p.key = '{_DISCOUNT_PERMISSION}'
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
        WHERE rp.permission_id = p.id AND p.key = '{_DISCOUNT_PERMISSION}'
        """
    )
    op.execute(f"DELETE FROM permission WHERE key = '{_DISCOUNT_PERMISSION}'")

    # Скидки исчезают вместе с колонками, а итог приёма уже посчитан с ними:
    # возвращаем его к сумме позиций, иначе чек «подешевеет» без объяснения.
    op.execute(
        "UPDATE appointment SET price_total_tiyin = price_total_tiyin + discount_tiyin "
        "WHERE discount_tiyin > 0"
    )
    op.drop_constraint(_APPOINTMENT_CHECK, "appointment", type_="check")
    op.drop_column("appointment", "discount_tiyin")
    op.drop_column("appointment", "discount_percent")

    op.drop_constraint(_ROLE_CHECK, "role", type_="check")
    op.drop_column("role", "max_discount_percent")
