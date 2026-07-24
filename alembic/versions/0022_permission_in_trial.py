"""Триал-функции: флаг доступности права на триале.

Добавляет ``permission.in_trial`` (BOOLEAN, дефолт ``true``). Существующие права
становятся доступными на триале (полный доступ по умолчанию); дальше набор
триал-функций настраивается из админки. Отдельной таблицы не нужно — это атрибут
уже существующего каталога прав.

Revision ID: 0022_permission_in_trial
Revises: 0021_cash
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_permission_in_trial"
down_revision: str | None = "0021_cash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "permission",
        sa.Column("in_trial", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("permission", "in_trial")
