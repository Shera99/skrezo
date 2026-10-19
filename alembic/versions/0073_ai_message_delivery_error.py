"""Почему WhatsApp не доставил сообщение — у самого сообщения в переписке.

Переписка 16.10.2026: бот четыре раза ответил пациенту, Meta четыре раза
отказала (номер не в списке разрешённых — тестовый режим), а в чате клиники не
осталось ни ответов бота, ни причины: только сообщения пациента без ответа.
Теперь недоставленное сообщение сохраняется с объяснением отказа, и сотрудник
видит в чате, что хотел сказать бот и почему это не дошло.

Revision ID: 0073_ai_message_delivery_error
Revises: 0072_slot_hold_note
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0073_ai_message_delivery_error"
down_revision = "0072_slot_hold_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_message", sa.Column("delivery_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_message", "delivery_error")
