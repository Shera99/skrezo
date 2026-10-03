"""Номер WhatsApp подключает сама клиника (Embedded Signup Meta).

* ``clinic_whatsapp`` — путь подключения, зашифрованный бизнес-токен и PIN
  клиники, кто и когда подключил, состояние номера, «нужно переподключить»,
  отметки синхронизации данных приложения WhatsApp Business;
* ``whatsapp_contact`` — адресная книга приложения WhatsApp Business клиники;
* ``whatsapp_history_message`` — архив переписки из приложения до подключения;
* право ``whatsapp.manage`` (группа settings): владелец имеет его всегда.

Revision ID: 0068_whatsapp_self_service
Revises: 0067_reminder_marks
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068_whatsapp_self_service"
down_revision = "0067_reminder_marks"
branch_labels = None
depends_on = None

_PERMISSION = "whatsapp.manage"
_PERMISSION_DESCRIPTION = "Подключение и отключение номера WhatsApp клиники"

_TS_COLUMNS = (
    "connected_at",
    "status_checked_at",
    "needs_reconnect_at",
    "reconnect_notified_at",
    "contacts_sync_requested_at",
    "history_sync_requested_at",
    "history_synced_at",
)
_TEXT_COLUMNS = (
    "access_token_enc",
    "two_step_pin_enc",
    "verified_name",
    "quality_rating",
    "needs_reconnect_reason",
    "history_error",
)


def upgrade() -> None:
    op.add_column(
        "clinic_whatsapp",
        sa.Column("connected_via", sa.Text(), nullable=False, server_default="manual"),
    )
    for name in _TEXT_COLUMNS:
        op.add_column("clinic_whatsapp", sa.Column(name, sa.Text(), nullable=True))
    for name in _TS_COLUMNS:
        op.add_column("clinic_whatsapp", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("clinic_whatsapp", sa.Column("history_progress", sa.Integer(), nullable=True))
    op.add_column(
        "clinic_whatsapp",
        sa.Column(
            "connected_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Вебхук account_update приходит с WABA, а не с номером: поиск привязки по нему.
    op.create_index("ix_clinic_whatsapp_waba", "clinic_whatsapp", ["waba_id"])

    op.create_table(
        "whatsapp_contact",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("wa_phone", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("clinic_id", "wa_phone", name="uq_whatsapp_contact_phone"),
    )

    op.create_table(
        "whatsapp_history_message",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("wa_message_id", sa.Text(), nullable=False, unique=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("media_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_whatsapp_history_conversation_sent",
        "whatsapp_history_message",
        ["conversation_id", "sent_at", "id"],
    )

    # На чистой БД право засеет каталог (0004), на живой — вставляем идемпотентно.
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_PERMISSION}', 'settings',
               '{_PERMISSION_DESCRIPTION}',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_PERMISSION}')
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM role_permission rp
        USING permission p
        WHERE rp.permission_id = p.id AND p.key = '{_PERMISSION}'
        """
    )
    op.execute(f"DELETE FROM permission WHERE key = '{_PERMISSION}'")
    op.drop_index("ix_whatsapp_history_conversation_sent", table_name="whatsapp_history_message")
    op.drop_table("whatsapp_history_message")
    op.drop_table("whatsapp_contact")
    op.drop_index("ix_clinic_whatsapp_waba", table_name="clinic_whatsapp")
    op.drop_column("clinic_whatsapp", "connected_by")
    op.drop_column("clinic_whatsapp", "history_progress")
    for name in (*_TS_COLUMNS, *_TEXT_COLUMNS):
        op.drop_column("clinic_whatsapp", name)
    op.drop_column("clinic_whatsapp", "connected_via")
