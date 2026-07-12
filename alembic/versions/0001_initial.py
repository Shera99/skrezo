"""Первичная схема: справочники, аккаунты, клиники, членства.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── ENUM-типы ────────────────────────────────────────────────────────────
    clinic_type = postgresql.ENUM("solo", "clinic", name="clinic_type", create_type=False)
    membership_role = postgresql.ENUM("admin", "doctor", name="membership_role", create_type=False)
    clinic_type.create(bind, checkfirst=True)
    membership_role.create(bind, checkfirst=True)

    # ── Справочники ──────────────────────────────────────────────────────────
    for table_name in ("city", "specialization"):
        op.create_table(
            table_name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    # ── Аккаунты (клиентский контур) ─────────────────────────────────────────
    op.create_table(
        "account",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("phone", sa.Text(), nullable=False, unique=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False, server_default="ru"),
        sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.false()),
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
    )

    # ── Клиники (тенант) ─────────────────────────────────────────────────────
    op.create_table(
        "clinic",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", clinic_type, nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("business_phone", sa.Text(), nullable=True),
        sa.Column("lang", sa.Text(), nullable=False, server_default="ru"),
        sa.Column("seats_limit", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["city_id"], ["city.id"], name="fk_clinic_city"),
    )

    # ── Членства (аккаунт ↔ клиника + роль) ─────────────────────────────────
    op.create_table(
        "membership",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("role", membership_role, nullable=False),
        sa.Column("specialization_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
            ["clinic_id"], ["clinic.id"], name="fk_membership_clinic", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name="fk_membership_account", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["specialization_id"], ["specialization.id"], name="fk_membership_specialization"
        ),
    )
    # Один активный админ на клинику (v1).
    op.create_index(
        "uq_one_active_admin_per_clinic",
        "membership",
        ["clinic_id"],
        unique=True,
        postgresql_where=sa.text("role = 'admin' AND active"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_active_admin_per_clinic", table_name="membership")
    op.drop_table("membership")
    op.drop_table("clinic")
    op.drop_table("account")
    op.drop_table("specialization")
    op.drop_table("city")
    postgresql.ENUM(name="membership_role").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="clinic_type").drop(op.get_bind(), checkfirst=True)
