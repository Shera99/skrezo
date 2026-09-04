"""Учёт использования по клиникам: токены модели и статусы WhatsApp.

* ``clinic_usage_day`` — дневные счётчики на клинику (UPSERT-инкременты):
  вызовы и токены модели, ответы ассистента, диалоги с ассистентом (каждый
  диалог считается один раз), исходящие WhatsApp по категориям платности Меты.
  Дневная гранулярность: месяц собирается суммой, объём — строка на клинику в
  день, а не строка на вызов (последних было бы до 180 тысяч в сутки);
* ``ai_conversation.assistant_counted_at`` — отметка «диалог уже посчитан»;
* ``ai_message.wa_status`` / ``wa_status_at`` / ``wa_pricing_category`` /
  ``wa_billable`` — из статусов доставки Меты: галочки в кабинете и
  себестоимость сообщения. Сумму Мета не присылает — только категорию и
  признак платности; в сомы переводит прайс-лист в настройках.

Revision ID: 0051_usage_metering
Revises: 0050_assistant_cabinet
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_usage_metering"
down_revision = "0050_assistant_cabinet"
branch_labels = None
depends_on = None

_COUNTERS = (
    "model_calls",
    "model_input_tokens",
    "model_output_tokens",
    "assistant_replies",
    "dialogs",
    "wa_sent",
    "wa_utility",
    "wa_marketing",
    "wa_authentication",
    "wa_billable_other",
    "wa_service_free",
)


def upgrade() -> None:
    op.create_table(
        "clinic_usage_day",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        *[
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
            for name in _COUNTERS
        ],
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("clinic_id", "day", name="uq_clinic_usage_day"),
    )
    op.create_index("ix_clinic_usage_day_day", "clinic_usage_day", ["day"])
    op.add_column(
        "ai_conversation",
        sa.Column("assistant_counted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("ai_message", sa.Column("wa_status", sa.Text(), nullable=True))
    op.add_column(
        "ai_message", sa.Column("wa_status_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("ai_message", sa.Column("wa_pricing_category", sa.Text(), nullable=True))
    op.add_column("ai_message", sa.Column("wa_billable", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_message", "wa_billable")
    op.drop_column("ai_message", "wa_pricing_category")
    op.drop_column("ai_message", "wa_status_at")
    op.drop_column("ai_message", "wa_status")
    op.drop_column("ai_conversation", "assistant_counted_at")
    op.drop_index("ix_clinic_usage_day_day", table_name="clinic_usage_day")
    op.drop_table("clinic_usage_day")
