"""Сид каталога прав и глобальных шаблонов ролей (Владелец/Менеджер/Работник).

Источник данных — ``app.modules.access.domain.permissions`` (единый источник
истины: тот же каталог использует провижининг при клонировании ролей клиники).

Revision ID: 0004_seed_rbac
Revises: 0003_rbac
Create Date: 2026-07-05
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.access.domain.permissions import (
    DEFAULT_ROLES,
    PERMISSION_CATALOG,
    resolve_permissions,
)

revision: str = "0004_seed_rbac"
down_revision: str | None = "0003_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_permission_t = sa.table(
    "permission",
    sa.column("id", sa.Uuid),
    sa.column("key", sa.Text),
    sa.column("group_key", sa.Text),
    sa.column("description", sa.Text),
    sa.column("sort_order", sa.Integer),
)
_role_t = sa.table(
    "role",
    sa.column("id", sa.Uuid),
    sa.column("clinic_id", sa.Uuid),
    sa.column("key", sa.Text),
    sa.column("name", sa.Text),
    sa.column("is_system", sa.Boolean),
    sa.column("is_owner", sa.Boolean),
)
_role_permission_t = sa.table(
    "role_permission",
    sa.column("role_id", sa.Uuid),
    sa.column("permission_id", sa.Uuid),
)


def upgrade() -> None:
    # Права
    permission_ids: dict[str, uuid.UUID] = {}
    permission_rows = []
    for i, perm in enumerate(PERMISSION_CATALOG):
        pid = uuid.uuid4()
        permission_ids[perm.key] = pid
        permission_rows.append(
            {
                "id": pid,
                "key": perm.key,
                "group_key": perm.group_key,
                "description": perm.description,
                "sort_order": i,
            }
        )
    op.bulk_insert(_permission_t, permission_rows)

    # Глобальные шаблоны ролей + их права
    role_rows = []
    role_permission_rows = []
    for role in DEFAULT_ROLES:
        rid = uuid.uuid4()
        role_rows.append(
            {
                "id": rid,
                "clinic_id": None,  # глобальный шаблон
                "key": role.key,
                "name": role.name,
                "is_system": True,
                "is_owner": role.is_owner,
            }
        )
        for perm_key in resolve_permissions(role):
            role_permission_rows.append({"role_id": rid, "permission_id": permission_ids[perm_key]})
    op.bulk_insert(_role_t, role_rows)
    op.bulk_insert(_role_permission_t, role_permission_rows)


def downgrade() -> None:
    op.execute("DELETE FROM role_permission")
    op.execute("DELETE FROM role WHERE clinic_id IS NULL")
    op.execute("DELETE FROM permission")
