"""Мультиязычность: lang_enum, таблицы переводов, пересев справочников (kg/kz/ru/en).

Revision ID: 0006_i18n
Revises: 0005_platform_user
Create Date: 2026-07-05
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.modules.catalog.domain.dictionaries import CITIES, SPECIALIZATIONS

revision: str = "0006_i18n"
down_revision: str | None = "0005_platform_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_lang_enum = postgresql.ENUM("kg", "kz", "ru", "en", name="lang_enum", create_type=False)


def _seed(
    base_table: sa.TableClause, tr_table: sa.TableClause, fk: str, entries: list[dict]
) -> None:
    base_rows = []
    tr_rows = []
    for i, translations in enumerate(entries):
        rid = uuid.uuid4()
        base_rows.append({"id": rid, "sort_order": i, "active": True})
        for lang, value in translations.items():
            tr_rows.append({"id": uuid.uuid4(), fk: rid, "lang": lang.value, "value": value})
    op.bulk_insert(base_table, base_rows)
    op.bulk_insert(tr_table, tr_rows)


def upgrade() -> None:
    bind = op.get_bind()
    _lang_enum.create(bind, checkfirst=True)

    # account.lang / clinic.lang: text → lang_enum
    for table in ("account", "clinic"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN lang DROP DEFAULT")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN lang TYPE lang_enum USING lang::lang_enum")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN lang SET DEFAULT 'ru'")

    # Таблицы переводов
    op.create_table(
        "city_translation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("lang", _lang_enum, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["city_id"], ["city.id"], name="fk_city_translation_city", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("city_id", "lang", name="uq_city_translation_lang"),
    )
    op.create_table(
        "specialization_translation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("specialization_id", sa.Uuid(), nullable=False),
        sa.Column("lang", _lang_enum, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["specialization_id"],
            ["specialization.id"],
            name="fk_specialization_translation_specialization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("specialization_id", "lang", name="uq_specialization_translation_lang"),
    )

    # Название уезжает из базовой таблицы в переводы — пересеваем справочники.
    op.execute("DELETE FROM specialization")
    op.execute("DELETE FROM city")
    op.drop_column("city", "name")
    op.drop_column("specialization", "name")

    city_t = sa.table(
        "city",
        sa.column("id", sa.Uuid),
        sa.column("sort_order", sa.Integer),
        sa.column("active", sa.Boolean),
    )
    city_tr_t = sa.table(
        "city_translation",
        sa.column("id", sa.Uuid),
        sa.column("city_id", sa.Uuid),
        sa.column("lang", _lang_enum),
        sa.column("value", sa.Text),
    )
    spec_t = sa.table(
        "specialization",
        sa.column("id", sa.Uuid),
        sa.column("sort_order", sa.Integer),
        sa.column("active", sa.Boolean),
    )
    spec_tr_t = sa.table(
        "specialization_translation",
        sa.column("id", sa.Uuid),
        sa.column("specialization_id", sa.Uuid),
        sa.column("lang", _lang_enum),
        sa.column("value", sa.Text),
    )
    _seed(city_t, city_tr_t, "city_id", CITIES)
    _seed(spec_t, spec_tr_t, "specialization_id", SPECIALIZATIONS)


def downgrade() -> None:
    # Возвращаем колонку name из ru-перевода.
    op.add_column("city", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("specialization", sa.Column("name", sa.Text(), nullable=True))
    op.execute(
        "UPDATE city c SET name = t.value FROM city_translation t "
        "WHERE t.city_id = c.id AND t.lang = 'ru'"
    )
    op.execute(
        "UPDATE specialization s SET name = t.value FROM specialization_translation t "
        "WHERE t.specialization_id = s.id AND t.lang = 'ru'"
    )
    op.alter_column("city", "name", nullable=False)
    op.alter_column("specialization", "name", nullable=False)

    op.drop_table("specialization_translation")
    op.drop_table("city_translation")

    for table in ("clinic", "account"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN lang DROP DEFAULT")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN lang TYPE text USING lang::text")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN lang SET DEFAULT 'ru'")

    postgresql.ENUM(name="lang_enum").drop(op.get_bind(), checkfirst=True)
