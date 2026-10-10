"""Заявки с лендинга и из приложения — одна очередь («одна очередь, две двери»).

Лид с лендинга (``POST /leads``, телефон без OTP) жил в отдельной таблице
``lead``, которую никто не видел: у неё не было ни одной читающей ручки. Теперь
он ложится в ``clinic_application`` — туда же, куда падают заявки через OTP, — с
пометкой «телефон не подтверждён». Таблица ``lead`` переезжает целиком и
удаляется.

В ``clinic_application``:

* ``company_name`` / ``city_id`` / ``doctors_count`` становятся nullable (форма
  лендинга спрашивает только имя и телефон);
* новые колонки ``full_name``, ``phone_verified``, ``source``, ``comment``,
  ``converted_clinic_id`` (конверсия для дашборда переехала из ``lead``);
* ``phone_verified`` добавляется с ``server_default true`` (все существующие
  заявки пришли через OTP), после чего значение по умолчанию снимается — обе
  двери задают признак явно;
* ``ck_clinic_application_verified_complete`` — заявка с подтверждённым
  телефоном всегда полная;
* ``ck_clinic_application_lead_named`` — у лида имя обязательно;
* частичный уникальный индекс ``ux_clinic_application_pending_lead_phone`` —
  защита дедупа лидов от гонки (открытый лид на номер один; заявки через OTP
  могут повторяться).

Перенос данных: каждая строка ``lead`` становится заявкой ``pending`` с
``phone_verified = false`` (разбирать их было некому — панели не существовало), id
и дата создания сохраняются. ``doctors_count_range`` — свободный текст — попадает
в enum, только если после ``trim`` это ровно ``2-5`` | ``6-10`` | ``10+``, иначе
``NULL``.

``downgrade`` воссоздаёт ``lead`` и возвращает туда неподтверждённые заявки (по
одной на телефон — самую раннюю); статус рассмотрения и источник теряются, потому
что в ``lead`` им некуда деться. Заявки через OTP остаются в ``clinic_application``.

Выкатка: миграция несовместима со старым кодом (``lead`` удалена, у
``phone_verified`` нет значения по умолчанию), а ``deploy.sh`` сначала мигрирует и
лишь потом по одному перезапускает API. В это окно старые процессы отвечают 500 на
``POST /leads``, ``POST /clinic-applications`` и дашборд. Катить в тихое окно и
предупредить фронт; для выкатки без простоя нужен expand/contract (оставить
``server_default true`` и таблицу ``lead`` до следующей миграции).

Revision ID: 0070_leads_into_applications
Revises: 0069_subscription_plans
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0070_leads_into_applications"
down_revision = "0069_subscription_plans"
branch_labels = None
depends_on = None

_TABLE = "clinic_application"
_CK_COMPLETE = "ck_clinic_application_verified_complete"
_CK_NAMED = "ck_clinic_application_lead_named"
_UX_PENDING_LEAD = "ux_clinic_application_pending_lead_phone"
_FK_CONVERTED = "fk_clinic_application_converted_clinic"


def upgrade() -> None:
    # 1. Поля, которых у лида нет, становятся необязательными.
    op.alter_column(_TABLE, "company_name", existing_type=sa.Text(), nullable=True)
    op.alter_column(_TABLE, "city_id", existing_type=sa.Uuid(), nullable=True)
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN doctors_count DROP NOT NULL")

    # 2. Новые колонки. Все прежние заявки пришли через OTP → phone_verified = true.
    op.add_column(_TABLE, sa.Column("full_name", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column(_TABLE, "phone_verified", server_default=None)
    op.add_column(_TABLE, sa.Column("source", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("comment", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "converted_clinic_id",
            sa.Uuid(),
            sa.ForeignKey("clinic.id", ondelete="SET NULL", name=_FK_CONVERTED),
            nullable=True,
        ),
    )

    # 3. Инварианты: проверяются и при переносе лидов ниже.
    op.create_check_constraint(
        _CK_COMPLETE,
        _TABLE,
        "phone_verified = false OR (company_name IS NOT NULL "
        "AND city_id IS NOT NULL AND doctors_count IS NOT NULL)",
    )
    op.create_check_constraint(_CK_NAMED, _TABLE, "phone_verified = true OR full_name IS NOT NULL")
    op.create_index(
        _UX_PENDING_LEAD,
        _TABLE,
        ["phone"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND phone_verified = false"),
    )

    # 4. Лиды → заявки. lead.phone уникален, значит, под частичный индекс
    #    переносимые строки конфликтовать не могут.
    op.execute(
        """
        INSERT INTO clinic_application (
            id, full_name, phone, company_name, doctors_count, comment,
            converted_clinic_id, phone_verified, status, source,
            created_at, updated_at
        )
        SELECT
            id,
            full_name,
            phone,
            clinic_name,
            CASE
                WHEN trim(doctors_count_range) IN ('2-5', '6-10', '10+')
                THEN trim(doctors_count_range)::doctors_count_range
            END,
            comment,
            converted_clinic_id,
            false,
            'pending',
            NULL,
            created_at,
            created_at
        FROM lead
        """
    )
    op.drop_table("lead")


def downgrade() -> None:
    # 1. Таблица лидов — как в 0008_lead.
    op.create_table(
        "lead",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False, unique=True),
        sa.Column("clinic_name", sa.Text(), nullable=False),
        sa.Column("doctors_count_range", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("converted_clinic_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["converted_clinic_id"], ["clinic.id"], name="fk_lead_converted_clinic"
        ),
    )

    # 2. Неподтверждённые заявки возвращаются в lead: на телефон — одна, самая
    #    ранняя (в lead он уникален). Название клиники в lead обязательно, поэтому
    #    без него подставляется имя контакта.
    op.execute(
        """
        INSERT INTO lead (
            id, full_name, phone, clinic_name, doctors_count_range, comment,
            converted_clinic_id, created_at
        )
        SELECT DISTINCT ON (phone)
            id,
            full_name,
            phone,
            coalesce(company_name, full_name),
            doctors_count::text,
            comment,
            converted_clinic_id,
            created_at
        FROM clinic_application
        WHERE phone_verified = false
        ORDER BY phone, created_at ASC, id
        """
    )
    op.execute("DELETE FROM clinic_application WHERE phone_verified = false")

    # 3. Снимаем всё, что добавил upgrade, и возвращаем NOT NULL (остались только
    #    заявки через OTP — по CHECK они полные).
    op.drop_index(_UX_PENDING_LEAD, table_name=_TABLE)
    op.drop_constraint(_CK_NAMED, _TABLE, type_="check")
    op.drop_constraint(_CK_COMPLETE, _TABLE, type_="check")
    op.drop_column(_TABLE, "converted_clinic_id")
    op.drop_column(_TABLE, "comment")
    op.drop_column(_TABLE, "source")
    op.drop_column(_TABLE, "phone_verified")
    op.drop_column(_TABLE, "full_name")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN doctors_count SET NOT NULL")
    op.alter_column(_TABLE, "city_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column(_TABLE, "company_name", existing_type=sa.Text(), nullable=False)
