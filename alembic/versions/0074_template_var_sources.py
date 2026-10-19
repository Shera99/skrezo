"""Источники значений переменных шаблона WhatsApp — для предзаполнения при отправке.

Meta не подставляет значения ``{{1}}``, ``{{2}}`` сама, а синхронизация знает
о переменной только номер: сотрудник вводил «имя пациента» и «название
клиники» руками перед каждой отправкой (19.10.2026). Теперь у переменной
есть источник (имя пациента, клиника, ближайшая запись, долг), и бэк
заполняет значения для диалога заранее. Пустой список — все «вручную».

Revision ID: 0074_template_var_sources
Revises: 0073_ai_message_delivery_error
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0074_template_var_sources"
down_revision = "0073_ai_message_delivery_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_template",
        sa.Column(
            "variable_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_template", "variable_sources")
