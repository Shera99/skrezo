"""Блокировка аккаунта: отобрать доступ человеку целиком.

До этого доступ отбирался только по клиникам — исключением членства. Человека,
которому нельзя входить вовсе (уволен со скандалом, номер увели), останавливать
было нечем: аккаунт глобальный, и он заходил по коду в любую другую клинику,
где числится.

``blocked_at`` — дата, а не флаг: «когда заблокировали» спрашивают в поддержке
первым вопросом, и держать это отдельно негде. NULL = аккаунт действующий.

Revision ID: 0039_account_blocking
Revises: 0038_otp_only_for_clinics
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_account_blocking"
down_revision: str | None = "0038_otp_only_for_clinics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("account", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("account", sa.Column("blocked_reason", sa.Text(), nullable=True))
    # Частичный индекс: заблокированных единицы, а «покажи заблокированных» —
    # регулярный запрос админки. Полный индекс по колонке был бы почти весь NULL.
    op.create_index(
        "ix_account_blocked_at",
        "account",
        ["blocked_at"],
        postgresql_where=sa.text("blocked_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_account_blocked_at", table_name="account")
    op.drop_column("account", "blocked_reason")
    op.drop_column("account", "blocked_at")
