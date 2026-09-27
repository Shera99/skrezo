"""Шаблоны WhatsApp: понятное название, подписи переменных, момент смены статуса.

Meta отдаёт только техническое имя (``visit_reminder``) и плейсхолдеры
``{{1}}``, ``{{2}}``. Названия и подписи переменных общих шаблонов платформы
пишет команда Skrezo в админке (``PATCH /admin/whatsapp/templates/{id}``);
у шаблонов клиники показывается имя из Meta. ``status_changed_at`` заполняет
синхронизация при смене статуса — из него складывается причина
недоступности вида «на проверке в Meta с 20 сентября». У строк, сверенных до
появления поля, момент неизвестен — остаётся ``NULL``.

Revision ID: 0066_whatsapp_template_labels
Revises: 0065_drop_service_templates
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0066_whatsapp_template_labels"
down_revision = "0065_drop_service_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("whatsapp_template", sa.Column("title", sa.Text(), nullable=True))
    op.add_column(
        "whatsapp_template",
        sa.Column(
            "variable_labels",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "whatsapp_template",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_template", "status_changed_at")
    op.drop_column("whatsapp_template", "variable_labels")
    op.drop_column("whatsapp_template", "title")
