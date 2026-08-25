"""Пуш-уведомления: устройства, префы и идемпотентность (инкремент P0-a).

Три таблицы-фундамент без транспорта и диспетчера (это следующие инкременты,
см. ``docs/PUSH_NOTIFICATIONS.md``):

* ``device`` — токен устройства, привязан к членству (account + clinic); один
  человек может держать несколько устройств.
* ``notification_pref`` — какие типы пушей отключены и тихие часы, одна
  строка на членство; отсутствие строки — дефолт (ничего не отключено).
* ``push_event`` — идемпотентность/дедуп для времязависимых пушей (скан «уже
  слали?»); в этом инкременте только таблица, писать и читать её будет
  ``PushDispatcher`` позже.

Revision ID: 0041_push_notifications
Revises: 0040_service_doctor
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_push_notifications"
down_revision: str | None = "0040_service_doctor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# lang_enum создан в 0006_i18n и уже используется account.lang/clinic.lang —
# здесь только ссылаемся на существующий тип, не создаём заново.
_lang_enum = postgresql.ENUM("kg", "kz", "ru", "en", name="lang_enum", create_type=False)


def upgrade() -> None:
    op.create_table(
        "device",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("platform", sa.Enum("ios", "android", name="device_platform"), nullable=False),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("locale", _lang_enum, nullable=False, server_default="ru"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_device_token"),
    )
    # Уборка устройств уволенного и фан-аут «на все устройства клиники» (sync.day).
    op.create_index("ix_device_account", "device", ["account_id"])
    op.create_index("ix_device_clinic", "device", ["clinic_id"])

    op.create_table(
        "notification_pref",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column(
            "disabled",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("quiet_from", sa.Time(), nullable=True),
        sa.Column("quiet_to", sa.Time(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "clinic_id", name="uq_notification_pref_membership"),
    )

    op.create_table(
        "push_event",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_push_event_clinic", "push_event", ["clinic_id"])


def downgrade() -> None:
    op.drop_index("ix_push_event_clinic", table_name="push_event")
    op.drop_table("push_event")

    op.drop_table("notification_pref")

    op.drop_index("ix_device_clinic", table_name="device")
    op.drop_index("ix_device_account", table_name="device")
    op.drop_table("device")
    # Тип enum создаётся вместе с таблицей, но не удаляется вместе с ней.
    sa.Enum(name="device_platform").drop(op.get_bind(), checkfirst=True)
    # lang_enum нам не принадлежит (создан в 0006_i18n) — не трогаем.
