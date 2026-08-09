"""Журнал действий внутри клиники: ``audit_log.clinic_id`` и право ``audit.view``.

Аудит писался только для платформенной зоны, и клиника не могла ответить на
вопрос «кто это сделал»: кто отменил приём, кто дал скидку, кто провёл возврат
из кассы, кто убрал сотрудника. При том что скидка ограничена потолком роли, а
возврат — это деньги.

Колонка нужна потому, что прежний способ отобрать записи клиники (``entity_type
= 'clinic' AND entity_id = <id>``) работает только для действий Skrezo **над**
клиникой. У клиничных записей сущность другая — приём, платёж, пациент, — и
принадлежность к клинике хранить больше негде.

⚠️ Старые строки остаются с ``NULL``: их принадлежность по-прежнему выражена
через ``entity_type='clinic'``, и платформенный журнал (`GET /admin/audit`)
читает их как раньше. Заполняется колонка только клиничным контуром, поэтому
`/admin/audit` не начнёт вдруг показывать тысячи записей о приёмах.

Revision ID: 0036_clinic_audit
Revises: 0035_period_warning
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_clinic_audit"
down_revision: str | None = "0035_period_warning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSION = "audit.view"
_DESCRIPTION = "Журнал действий сотрудников клиники"


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("clinic_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_audit_log_clinic", "audit_log", "clinic", ["clinic_id"], ["id"], ondelete="CASCADE"
    )
    # Журнал всегда читают по одной клинике и с конца — индекс под этот порядок.
    op.create_index(
        "ix_audit_log_clinic_created",
        "audit_log",
        ["clinic_id", sa.literal_column("created_at DESC")],
    )

    # На чистой БД право засеет 0004 из каталога, на живой — вставляем здесь.
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_PERMISSION}', 'audit', '{_DESCRIPTION}',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_PERMISSION}')
        """
    )
    # Менеджеру — по каталогу ролей; владелец получает всё по признаку is_owner.
    op.execute(
        f"""
        INSERT INTO role_permission (role_id, permission_id)
        SELECT r.id, p.id
        FROM role r, permission p
        WHERE r.key = 'manager' AND p.key = '{_PERMISSION}'
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
        WHERE rp.permission_id = p.id AND p.key = '{_PERMISSION}'
        """
    )
    op.execute(f"DELETE FROM permission WHERE key = '{_PERMISSION}'")
    op.drop_index("ix_audit_log_clinic_created", table_name="audit_log")
    op.drop_constraint("fk_audit_log_clinic", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "clinic_id")
