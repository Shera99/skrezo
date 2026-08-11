"""Вход по паролю остаётся только у команды Skrezo.

Клиники и соло-врачи переходят на вход по коду из SMS, поэтому хеши паролей
всех аккаунтов с ``is_platform_admin = false`` обнуляются: пароль, которым
нельзя войти, — это мёртвый секрет в базе и расхождение между тем, что видно в
данных, и тем, что делает код. После миграции инвариант однозначен:
``password_hash IS NOT NULL`` ⇔ аккаунт команды Skrezo.

Заодно ``must_change_password`` переезжает на ``server_default = false``: флаг
осмыслен только там, где есть пароль, и «по умолчанию требуется смена» для
passwordless-аккаунта — неверное состояние.

Откат восстанавливает только server_default. Хеши не возвращаются: argon2
необратим, и downgrade оставит клиничные аккаунты без пароля — им нужно будет
выдать пароли заново (``app/scripts/create_platform_user.py`` или сброс из
админки, если её вернут).

Revision ID: 0038_otp_only_for_clinics
Revises: 0037_patient_files
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_otp_only_for_clinics"
down_revision: str | None = "0037_patient_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE account "
            "SET password_hash = NULL, must_change_password = false "
            "WHERE is_platform_admin = false"
        )
    )
    # Аккаунты теперь заводятся без пароля — «требуется смена» по умолчанию
    # означало бы, что каждый новый сотрудник обязан сменить несуществующий пароль.
    op.alter_column(
        "account",
        "must_change_password",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("false"),
    )


def downgrade() -> None:
    op.alter_column(
        "account",
        "must_change_password",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("true"),
    )
