"""События и инбокс (Ф3, ядро): транзакционный outbox + снимок адресатов.

Одно событие → один инбокс → доставки (ТЗ-СУ). Здесь фундамент:

* ``event`` — outbox: пишется в одной транзакции с доменным изменением,
  диспетчер воркера разбирает по ``NOTIFY`` и 30-секундному поллингу;
* ``inbox_item`` — источник истины «что произошло и прочитано ли»; адресаты —
  снимок (аккаунт + клиника), уникальность ``(event_id, account_id)`` даёт
  идемпотентность повторной обработки.

Revision ID: 0043_events_inbox
Revises: 0042_whatsapp_channel
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_events_inbox"
down_revision: str | None = "0042_whatsapp_channel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_lang_enum = postgresql.ENUM("kg", "kz", "ru", "en", name="lang_enum", create_type=False)


def upgrade() -> None:
    op.create_table(
        "event",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_pending",
        "event",
        ["id"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.create_index("ix_event_clinic_occurred", "event", ["clinic_id", "occurred_at"])

    op.create_table(
        "inbox_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column(
            "priority",
            sa.Enum("critical", "normal", "low", name="event_priority"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("lang", _lang_enum, nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "account_id", name="uq_inbox_item_event_account"),
    )
    op.create_index("ix_inbox_item_owner", "inbox_item", ["account_id", "clinic_id", "id"])
    op.create_index(
        "ix_inbox_item_unread",
        "inbox_item",
        ["account_id", "clinic_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_item_unread", table_name="inbox_item")
    op.drop_index("ix_inbox_item_owner", table_name="inbox_item")
    op.drop_table("inbox_item")

    op.drop_index("ix_event_clinic_occurred", table_name="event")
    op.drop_index("ix_event_pending", table_name="event")
    op.drop_table("event")

    sa.Enum(name="event_priority").drop(op.get_bind(), checkfirst=True)
    # lang_enum нам не принадлежит (создан в 0006_i18n) — не трогаем.
