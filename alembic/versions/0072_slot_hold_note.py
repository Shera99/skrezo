"""Пожелание пациента в мягком резерве записи — комментарий к будущему приёму.

Ассистент записывал пациента, но жалобу, номер зуба или вторую процедуру
(«18 зуб болит», «ещё и чистку») сохранить было некуда — бот отвечал
отказом в канцелярите, а врач узнавал о жалобе только на приёме (переписка
13.10.2026). Модель передаёт эти слова пациента в ``propose_appointment``,
резерв хранит их до нажатия кнопки, и они ложатся в комментарий приёма.

Revision ID: 0072_slot_hold_note
Revises: 0071_usage_voice_counters
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0072_slot_hold_note"
down_revision = "0071_usage_voice_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("slot_hold", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("slot_hold", "note")
