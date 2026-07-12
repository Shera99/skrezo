"""Выдать право staff.manage всем ролям «менеджер» (глобальным и по клиникам).

Менеджер может заводить/вести работников наравне с владельцем.

Revision ID: 0013_mgr_staff
Revises: 0012_support_notes
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_mgr_staff"
down_revision: str | None = "0012_support_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permission (role_id, permission_id)
        SELECT r.id, p.id
        FROM role r, permission p
        WHERE r.key = 'manager' AND p.key = 'staff.manage'
          AND NOT EXISTS (
              SELECT 1 FROM role_permission rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permission rp
        USING role r, permission p
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
          AND r.key = 'manager' AND p.key = 'staff.manage'
        """
    )
