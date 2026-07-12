"""Пароль аккаунта: password_hash + must_change_password.

Переход клиентского контура с OTP-входа на вход по телефону + паролю.
Существующие аккаунты получают password_hash = NULL (пароль не задан) и
must_change_password = true.

Revision ID: 0015_account_password
Revises: 0014_audit_log
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_account_password"
down_revision: str | None = "0014_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("account", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "account",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("account", "must_change_password")
    op.drop_column("account", "password_hash")
