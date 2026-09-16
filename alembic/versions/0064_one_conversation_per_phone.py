"""Один диалог WhatsApp на пару (клиника, номер): дубли слиты, индекс без условия.

Раньше активный диалог на номер был один (partial unique по ``status =
'active'``), а закрытый после суток тишины оставался в списке, и следующее
сообщение пациента заводило новую строку — в кабинете копились «отдельные
чаты» одного человека. Теперь диалог постоянный: закрытый возобновляется
следующим сообщением (``whatsapp.domain.lifecycle.reopen_if_closed``), а
уникальность держит обычный индекс по (clinic_id, wa_phone).

Перед индексом дубли сливаются в один диалог-выживший на пару: незакрытый,
иначе свежий по последнему сообщению. К нему переезжают сообщения, эскалации,
вызовы инструментов, резервы слотов, резюме и напоминания; метки последних
сообщений и пациент — по максимуму группы. Слияние необратимо: downgrade
возвращает только прежний partial-индекс.

Revision ID: 0064_one_conversation_per_phone
Revises: 0063_clinic_ledger
"""

from __future__ import annotations

from alembic import op

revision = "0064_one_conversation_per_phone"
down_revision = "0063_clinic_ledger"
branch_labels = None
depends_on = None

# Таблицы со ссылкой на диалог: их строки переезжают к выжившему.
_CHILD_TABLES = (
    "ai_message",
    "ai_escalation",
    "ai_tool_call",
    "slot_hold",
    "ai_summary",
    "appointment_reminder",
    "patient_reminder",
)


def upgrade() -> None:
    # Выживший в группе — первый по порядку: незакрытый, свежий, старший.
    op.execute(
        """
        CREATE TEMP TABLE _conv_dups AS
        SELECT id, survivor_id
        FROM (
            SELECT id,
                   first_value(id) OVER (
                       PARTITION BY clinic_id, wa_phone
                       ORDER BY (status = 'closed') ASC, last_msg_at DESC, created_at DESC, id
                   ) AS survivor_id
            FROM ai_conversation
        ) AS ranked
        WHERE id <> survivor_id
        """
    )
    for table in _CHILD_TABLES:
        op.execute(
            f"""
            UPDATE {table} AS child
            SET conversation_id = dups.survivor_id
            FROM _conv_dups AS dups
            WHERE child.conversation_id = dups.id
            """
        )
    # GREATEST в Postgres пропускает NULL: пустая метка дубля не затирает живую.
    op.execute(
        """
        UPDATE ai_conversation AS survivor
        SET last_msg_at = GREATEST(survivor.last_msg_at, merged.last_msg_at),
            last_patient_msg_at = GREATEST(
                survivor.last_patient_msg_at, merged.last_patient_msg_at
            ),
            patient_id = COALESCE(survivor.patient_id, merged.patient_id)
        FROM (
            SELECT dups.survivor_id,
                   MAX(dup.last_msg_at) AS last_msg_at,
                   MAX(dup.last_patient_msg_at) AS last_patient_msg_at,
                   (MAX(dup.patient_id::text))::uuid AS patient_id
            FROM _conv_dups AS dups
            JOIN ai_conversation AS dup ON dup.id = dups.id
            GROUP BY dups.survivor_id
        ) AS merged
        WHERE survivor.id = merged.survivor_id
        """
    )
    op.execute(
        "DELETE FROM ai_conversation AS conv USING _conv_dups AS dups WHERE conv.id = dups.id"
    )
    op.execute("DROP TABLE _conv_dups")
    op.drop_index("uq_ai_conversation_active", table_name="ai_conversation")
    op.create_index(
        "uq_ai_conversation_phone", "ai_conversation", ["clinic_id", "wa_phone"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_ai_conversation_phone", table_name="ai_conversation")
    op.create_index(
        "uq_ai_conversation_active",
        "ai_conversation",
        ["clinic_id", "wa_phone"],
        unique=True,
        postgresql_where="status = 'active'",
    )
