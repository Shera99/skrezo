"""WhatsApp-канал (Ф1): привязка номера, диалоги, сообщения, шаблоны, право.

Транспорт без интеллекта (ассистент — Ф2, его поля заведены сразу, чтобы не
мигрировать живую переписку):

* ``clinic_whatsapp`` — словарь «номер Meta → клиника» для вебхука;
* ``ai_conversation`` — диалог: один активный на пару (клиника, номер);
* ``ai_message`` — сообщения; ``wa_message_id`` уникален (дедуп вебхука);
* ``whatsapp_template`` — одобренные шаблоны для сообщений вне 24-часового окна;
* право ``conversations.manage`` — переписка доступна владельцу и менеджеру
  (Р22: врач по умолчанию пациентам не отвечает).

Revision ID: 0042_whatsapp_channel
Revises: 0041_push_notifications
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_whatsapp_channel"
down_revision: str | None = "0041_push_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# lang_enum создан в 0006_i18n — только ссылаемся, не создаём.
_lang_enum = postgresql.ENUM("kg", "kz", "ru", "en", name="lang_enum", create_type=False)

_PERMISSION = "conversations.manage"
_PERMISSION_DESCRIPTION = "Переписка WhatsApp с пациентами"


def upgrade() -> None:
    op.create_table(
        "clinic_whatsapp",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number_id", sa.Text(), nullable=False),
        sa.Column("display_phone", sa.Text(), nullable=False),
        sa.Column("waba_id", sa.Text(), nullable=True),
        sa.Column(
            "mode",
            sa.Enum("api_only", "coexistence", name="whatsapp_mode"),
            nullable=False,
            server_default="api_only",
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", name="uq_clinic_whatsapp_clinic"),
        sa.UniqueConstraint("phone_number_id", name="uq_clinic_whatsapp_phone_number"),
    )

    op.create_table(
        "ai_conversation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("wa_phone", sa.Text(), nullable=False),
        sa.Column("lang", _lang_enum, nullable=False, server_default="ru"),
        sa.Column(
            "status",
            sa.Enum("active", "closed", "escalated", name="ai_conversation_status"),
            nullable=False,
        ),
        sa.Column(
            "last_msg_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_patient_msg_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Один активный диалог на номер в пределах клиники.
    op.create_index(
        "uq_ai_conversation_active",
        "ai_conversation",
        ["clinic_id", "wa_phone"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_ai_conversation_clinic_last", "ai_conversation", ["clinic_id", "last_msg_at"]
    )

    op.create_table(
        "ai_message",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("patient", "bot", "admin", "system", name="ai_message_role"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("wa_message_id", sa.Text(), nullable=True),
        sa.Column("media_type", sa.Text(), nullable=True),
        sa.Column("media_id", sa.Text(), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("media_mime", sa.Text(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("used_facts", postgresql.JSONB(), nullable=True),
        sa.Column("validator", postgresql.JSONB(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wa_message_id", name="uq_ai_message_wa_message_id"),
    )
    op.create_index("ix_ai_message_conversation_id", "ai_message", ["conversation_id", "id"])

    op.create_table(
        "whatsapp_template",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=True),
        sa.Column("meta_name", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=False),
        sa.Column(
            "variables", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
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
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_whatsapp_template_clinic",
        "whatsapp_template",
        ["clinic_id", "meta_name", "lang"],
        unique=True,
        postgresql_where=sa.text("clinic_id IS NOT NULL"),
    )
    op.create_index(
        "uq_whatsapp_template_common",
        "whatsapp_template",
        ["meta_name", "lang"],
        unique=True,
        postgresql_where=sa.text("clinic_id IS NULL"),
    )

    # Право на переписку: на чистой БД его засеет 0004 из каталога, на живой —
    # вставляем здесь. Обе вставки идемпотентны (паттерн 0030_discount).
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_PERMISSION}', 'conversations',
               '{_PERMISSION_DESCRIPTION}',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_PERMISSION}')
        """
    )
    # Владельцу (всегда всё) и менеджеру — и шаблонам ролей, и клонам клиник.
    op.execute(
        f"""
        INSERT INTO role_permission (role_id, permission_id)
        SELECT r.id, p.id
        FROM role r, permission p
        WHERE p.key = '{_PERMISSION}'
          AND (r.is_owner OR r.key = 'manager')
          AND NOT EXISTS (
              SELECT 1 FROM role_permission rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
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

    op.drop_index("uq_whatsapp_template_common", table_name="whatsapp_template")
    op.drop_index("uq_whatsapp_template_clinic", table_name="whatsapp_template")
    op.drop_table("whatsapp_template")

    op.drop_index("ix_ai_message_conversation_id", table_name="ai_message")
    op.drop_table("ai_message")

    op.drop_index("ix_ai_conversation_clinic_last", table_name="ai_conversation")
    op.drop_index("uq_ai_conversation_active", table_name="ai_conversation")
    op.drop_table("ai_conversation")

    op.drop_table("clinic_whatsapp")

    # Типы enum создаются вместе с таблицами, но не удаляются вместе с ними.
    sa.Enum(name="ai_message_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ai_conversation_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="whatsapp_mode").drop(op.get_bind(), checkfirst=True)
    # lang_enum нам не принадлежит (создан в 0006_i18n) — не трогаем.
