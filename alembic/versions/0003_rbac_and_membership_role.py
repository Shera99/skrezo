"""RBAC клиники: permission/role/role_permission + membership.role → role_id.

Revision ID: 0003_rbac
Revises: 0002_seed_catalog
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_rbac"
down_revision: str | None = "0002_seed_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Каталог прав ─────────────────────────────────────────────────────────
    op.create_table(
        "permission",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── Роли (пер-клиника; clinic_id IS NULL — глобальный шаблон) ────────────
    op.create_table(
        "role",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), nullable=True),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id"], ["clinic.id"], name="fk_role_clinic", ondelete="CASCADE"
        ),
    )

    # ── Связка роль ↔ право ──────────────────────────────────────────────────
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name="fk_role_permission_role", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            name="fk_role_permission_permission",
            ondelete="CASCADE",
        ),
    )

    # ── membership: enum role → FK role_id ───────────────────────────────────
    op.drop_index("uq_one_active_admin_per_clinic", table_name="membership")
    op.drop_column("membership", "role")
    # Данных ещё нет — добавляем NOT NULL сразу.
    op.add_column("membership", sa.Column("role_id", sa.Uuid(), nullable=False))
    op.create_foreign_key("fk_membership_role", "membership", "role", ["role_id"], ["id"])
    postgresql.ENUM(name="membership_role").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    membership_role = postgresql.ENUM("admin", "doctor", name="membership_role", create_type=False)
    membership_role.create(bind, checkfirst=True)

    op.drop_constraint("fk_membership_role", "membership", type_="foreignkey")
    op.drop_column("membership", "role_id")
    op.add_column("membership", sa.Column("role", membership_role, nullable=False))
    op.create_index(
        "uq_one_active_admin_per_clinic",
        "membership",
        ["clinic_id"],
        unique=True,
        postgresql_where=sa.text("role = 'admin' AND active"),
    )

    op.drop_table("role_permission")
    op.drop_table("role")
    op.drop_table("permission")
