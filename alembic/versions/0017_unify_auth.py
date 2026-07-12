"""Единый вход: account.is_platform_admin + слияние platform_user в account.

Объединяет два контура авторизации в один. Команда Skrezo больше не хранится в
`platform_user` — становится аккаунтами с флагом `is_platform_admin`. Строки
`platform_user` зеркалируются в `account` С ТЕМ ЖЕ id (чтобы FK из `audit_log`
и `tariff_version` остались валидными), затем FK перецепляются на `account`, а
таблица `platform_user` удаляется. Телефон мигрированных админов — плейсхолдер
`skrezo:<login>` (реальный номер задаётся заново через create_platform_user).

Revision ID: 0017_unify_auth
Revises: 0016_tariff_packages
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_unify_auth"
down_revision: str | None = "0016_tariff_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Признак платформенного админа.
    op.add_column(
        "account",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 2) Зеркалируем команду Skrezo в account с тем же id (для сохранности FK).
    op.execute(
        """
        INSERT INTO account
            (id, phone, full_name, lang, onboarded,
             password_hash, must_change_password, is_platform_admin,
             created_at, updated_at)
        SELECT
            pu.id,
            'skrezo:' || pu.login,
            COALESCE(pu.full_name, 'Skrezo Admin'),
            'ru',
            true,
            pu.password_hash,
            false,
            true,
            now(),
            now()
        FROM platform_user pu
        ON CONFLICT (id) DO NOTHING
        """
    )

    # 3) Перецепляем FK с platform_user на account (имена констрейнтов — как в БД).
    op.drop_constraint("fk_audit_log_actor", "audit_log", type_="foreignkey")
    op.create_foreign_key("fk_audit_log_actor", "audit_log", "account", ["actor_id"], ["id"])
    op.drop_constraint("fk_tariff_version_created_by", "tariff_version", type_="foreignkey")
    op.create_foreign_key(
        "fk_tariff_version_created_by", "tariff_version", "account", ["created_by"], ["id"]
    )

    # 4) Таблица команды больше не нужна.
    op.drop_table("platform_user")


def downgrade() -> None:
    # Структурный откат (данные админов остаются в account как is_platform_admin).
    op.create_table(
        "platform_user",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("login", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.drop_constraint("fk_audit_log_actor", "audit_log", type_="foreignkey")
    op.create_foreign_key(
        "fk_audit_log_actor", "audit_log", "platform_user", ["actor_id"], ["id"]
    )
    op.drop_constraint("fk_tariff_version_created_by", "tariff_version", type_="foreignkey")
    op.create_foreign_key(
        "fk_tariff_version_created_by",
        "tariff_version",
        "platform_user",
        ["created_by"],
        ["id"],
    )
    op.drop_column("account", "is_platform_admin")
