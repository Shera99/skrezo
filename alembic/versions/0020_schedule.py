"""Расписание клиники: таймзона, длительность приёма, графики и защита от пересечений.

Добавляет всё, чего не хватало «ленте дня»:

* ``clinic.timezone`` — границы дня считаются в таймзоне клиники, а не в UTC;
* ``appointment.service_id`` — основная услуга приёма (источник длительности);
* ``appointment.duration_min`` — длительность (высота карточки); старым строкам — 30;
* ``clinic_working_hours`` — часы работы клиники и общий обед по дням недели;
* ``doctor_schedule`` — индивидуальный график врача по дням недели;
* ``doctor_time_off`` — отсутствия/отпуска врача (абсолютные интервалы);
* ``ex_appointment_doctor_overlap`` — EXCLUDE-констрейнт: у одного врача приёмы
  не пересекаются. Проверки в прикладном слое мало — два одновременных запроса
  займут один слот; констрейнт закрывает гонку на уровне БД.

Revision ID: 0020_schedule
Revises: 0019_clinical_core_v2
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_schedule"
down_revision: str | None = "0019_clinical_core_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OVERLAP_CONSTRAINT = "ex_appointment_doctor_overlap"
_INTERVAL_FN = "appointment_tstzrange"
# Длительность, которую получает вся существующая история приёмов.
_DEFAULT_DURATION_MIN = 30

# Дрейф схемы из 0019: ORM объявляет эти колонки NOT NULL (TimestampMixin и
# Appointment.scheduled_at), а таблицы созданы без ограничения. Пока БД
# позволяет NULL, любой PATCH со `scheduledAt: null` необратимо ломает врача:
# tstzrange(NULL, NULL) = (,) — неограниченный диапазон, пересекающийся со всем.
# (table, column) → чем заполнить существующие NULL перед SET NOT NULL.
_NOT_NULL_FIXES: list[tuple[str, str, str]] = [
    ("appointment", "created_at", "now()"),
    ("appointment", "updated_at", "COALESCE(created_at, now())"),
    # scheduled_at: момент заведения записи — единственный осмысленный ориентир.
    ("appointment", "scheduled_at", "COALESCE(created_at, now())"),
    ("appointment_tooth", "created_at", "now()"),
    ("patient", "created_at", "now()"),
    ("patient", "updated_at", "COALESCE(created_at, now())"),
    ("patient_tooth", "created_at", "now()"),
    ("patient_tooth", "updated_at", "COALESCE(created_at, now())"),
    ("service", "created_at", "now()"),
    ("service", "updated_at", "COALESCE(created_at, now())"),
]


# Пересекающиеся пары приёмов у одного врача — ровно по тому же выражению, что и
# в EXCLUDE-констрейнте, чтобы проверка и констрейнт считали пересечение одинаково.
# ``b.id > a.id`` — чтобы пара считалась один раз, а не дважды.
_OVERLAP_PROBE = """
    SELECT count(*) AS pairs,
           count(DISTINCT a.doctor_membership_id) AS doctors
    FROM appointment a
    JOIN appointment b
      ON b.doctor_membership_id = a.doctor_membership_id
     AND b.id > a.id
     AND appointment_tstzrange(b.scheduled_at, b.duration_min)
      && appointment_tstzrange(a.scheduled_at, a.duration_min)
    WHERE a.doctor_membership_id IS NOT NULL
      AND a.status <> 'cancelled'::appointment_status
      AND b.status <> 'cancelled'::appointment_status
"""

# Тот же предикат списком — для человека у консоли. ВАЖНО: этот запрос он выполнит
# уже ПОСЛЕ отката миграции, на схеме 0019, где нет ни duration_min, ни
# appointment_tstzrange. Поэтому здесь только то, что существует до 0020:
# длительность подставлена литералом, диапазон собран инлайном.
_OVERLAP_LIST_SQL = f"""
SELECT a.id AS приём_1, b.id AS приём_2, a.doctor_membership_id AS врач,
       a.scheduled_at AS время_1, b.scheduled_at AS время_2
FROM appointment a
JOIN appointment b
  ON b.doctor_membership_id = a.doctor_membership_id
 AND b.id > a.id
 AND tstzrange(a.scheduled_at, a.scheduled_at + interval '{_DEFAULT_DURATION_MIN} minutes')
  && tstzrange(b.scheduled_at, b.scheduled_at + interval '{_DEFAULT_DURATION_MIN} minutes')
WHERE a.doctor_membership_id IS NOT NULL
  AND a.status <> 'cancelled'::appointment_status
  AND b.status <> 'cancelled'::appointment_status
ORDER BY a.doctor_membership_id, a.scheduled_at;
""".strip()


def _check_no_existing_overlaps() -> None:
    """Упасть понятным сообщением, если история приёмов не переживёт констрейнт.

    До 0020 у приёма не было ни длительности, ни защиты от пересечений: записать
    врача на 09:00 и 09:10 было законно и рутинно (осмотр, консультация, доклейка).
    Бэкфилл выдаёт всей истории ``duration_min = 30`` — и такие пары задним числом
    становятся «пересечением», из-за чего ``ADD CONSTRAINT`` падает сырым
    ``ExclusionViolationError`` и деплой срывается. Данные при этом целы (DDL
    транзакционный), но причину надо объяснить здесь, а не заставлять человека
    гуглить текст ошибки Postgres.
    """
    row = op.get_bind().execute(sa.text(_OVERLAP_PROBE)).one()
    pairs, doctors = int(row.pairs), int(row.doctors)
    if not pairs:
        return
    raise RuntimeError(
        "\n"
        "Миграция 0020 остановлена: приёмы в базе не переживут констрейнт\n"
        f"«{_OVERLAP_CONSTRAINT}» (приёмы одного врача не пересекаются).\n"
        "\n"
        f"Найдено пересекающихся пар: {pairs}; врачей затронуто: {doctors}.\n"
        "\n"
        "Почему так. До этой миграции у приёма не было длительности и пересечения\n"
        "никто не запрещал — записать врача на 09:00 и 09:10 было нормально.\n"
        f"Миграция проставляет всей истории duration_min = {_DEFAULT_DURATION_MIN} минут,\n"
        "и такие соседние приёмы задним числом становятся пересечением.\n"
        "\n"
        "Миграция откачена целиком, данные не тронуты. Колонки duration_min сейчас\n"
        "ещё НЕТ — она появляется в этой же миграции, поэтому разруливать конфликты\n"
        "нужно через scheduled_at и status:\n"
        "  * развести время приёмов, чтобы у одного врача между началами было\n"
        f"    не меньше {_DEFAULT_DURATION_MIN} минут:\n"
        "      UPDATE appointment SET scheduled_at = '...' WHERE id = '...';\n"
        "  * отменить лишний приём (отменённые под констрейнт не попадают):\n"
        "      UPDATE appointment SET status = 'cancelled' WHERE id = '...';\n"
        "  * если это мусорные данные — удалить:\n"
        "      DELETE FROM appointment WHERE id = '...';\n"
        "Реальные длительности можно проставить уже после успешной миграции.\n"
        "\n"
        "Показать конфликты (запрос рассчитан на текущую схему):\n"
        f"{_OVERLAP_LIST_SQL}\n"
    )


def upgrade() -> None:
    # ── 1. Таймзона клиники ────────────────────────────────────────────────
    op.add_column(
        "clinic",
        sa.Column("timezone", sa.Text(), nullable=False, server_default=sa.text("'Asia/Bishkek'")),
    )

    # ── 2. Приём: основная услуга и длительность ───────────────────────────
    op.add_column("appointment", sa.Column("service_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_appointment_service_id",
        "appointment",
        "service",
        ["service_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # server_default проставит 30 существующим строкам и оставит разумный дефолт.
    op.add_column(
        "appointment",
        sa.Column(
            "duration_min",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(str(_DEFAULT_DURATION_MIN)),
        ),
    )

    # ── 3. Часы работы клиники ─────────────────────────────────────────────
    op.create_table(
        "clinic_working_hours",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        sa.Column("break_start", sa.Time(), nullable=True),
        sa.Column("break_end", sa.Time(), nullable=True),
        sa.Column("is_working", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("clinic_id", "weekday", name="uq_clinic_working_hours_weekday"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_clinic_working_hours_weekday"),
    )
    op.create_index("ix_clinic_working_hours_clinic_id", "clinic_working_hours", ["clinic_id"])

    # ── 4. График врача ────────────────────────────────────────────────────
    op.create_table(
        "doctor_schedule",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("is_working", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "clinic_id", "membership_id", "weekday", name="uq_doctor_schedule_weekday"
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_doctor_schedule_weekday"),
    )

    # ── 5. Отсутствия врача ────────────────────────────────────────────────
    op.create_table(
        "doctor_time_off",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Uuid(), sa.ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_doctor_time_off_range"),
    )
    op.create_index(
        "ix_doctor_time_off_lookup",
        "doctor_time_off",
        ["clinic_id", "membership_id", "starts_at", "ends_at"],
    )

    # ── 6. Календарь дня: индекс под выборку «приёмы клиники за день» ───────
    op.create_index(
        "ix_appointment_clinic_scheduled_at", "appointment", ["clinic_id", "scheduled_at"]
    )

    # ── 7. Закрыть дрейф схемы 0019: привести NOT NULL к ORM ───────────────
    # Порядок важен: сперва добиваем NULL в данных, потом вешаем ограничение.
    for table, column, fill in _NOT_NULL_FIXES:
        op.execute(f"UPDATE {table} SET {column} = {fill} WHERE {column} IS NULL")
        op.alter_column(table, column, nullable=False)

    # ── 8. Защита от гонок: приёмы одного врача не пересекаются ─────────────
    # btree_gist нужен, чтобы включить в GiST-индекс равенство по uuid.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # Выражение индекса обязано быть IMMUTABLE, а оператор timestamptz + interval
    # помечен лишь STABLE: интервал может нести дни/месяцы, а их смысл зависит от
    # GUC TimeZone. Но make_interval(mins => ...) даёт чистое время без дней и
    # месяцев — прибавление N минут к timestamptz абсолютно и от таймзоны не
    # зависит, поэтому обёртка честно помечается IMMUTABLE. Каст text→interval
    # ((duration_min || ' minutes')::interval) здесь неприменим — он не immutable.
    op.execute(
        f"""
        CREATE FUNCTION {_INTERVAL_FN}(scheduled_at timestamptz, duration_min integer)
        RETURNS tstzrange
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
            SELECT tstzrange(
                scheduled_at,
                scheduled_at + make_interval(mins => duration_min)
            )
        $$
        """
    )
    _check_no_existing_overlaps()

    # Отменённые приёмы слот не занимают и в проверку не попадают.
    op.execute(
        f"""
        ALTER TABLE appointment
        ADD CONSTRAINT {_OVERLAP_CONSTRAINT}
        EXCLUDE USING gist (
            doctor_membership_id WITH =,
            {_INTERVAL_FN}(scheduled_at, duration_min) WITH &&
        )
        WHERE (
            doctor_membership_id IS NOT NULL
            AND status <> 'cancelled'::appointment_status
        )
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE appointment DROP CONSTRAINT IF EXISTS {_OVERLAP_CONSTRAINT}")
    op.execute(f"DROP FUNCTION IF EXISTS {_INTERVAL_FN}(timestamptz, integer)")
    # btree_gist намеренно НЕ снимаем: расширение могло существовать до нас и им
    # может пользоваться что-то ещё — DROP уронил бы чужие индексы.
    # Расширения ставят, но не снимают.

    # Вернуть nullable, как было в 0019 (данные не трогаем — они уже корректны).
    for table, column, _ in reversed(_NOT_NULL_FIXES):
        op.alter_column(table, column, nullable=True)

    op.drop_index("ix_appointment_clinic_scheduled_at", table_name="appointment")
    op.drop_index("ix_doctor_time_off_lookup", table_name="doctor_time_off")
    op.drop_table("doctor_time_off")
    op.drop_table("doctor_schedule")
    op.drop_index("ix_clinic_working_hours_clinic_id", table_name="clinic_working_hours")
    op.drop_table("clinic_working_hours")

    op.drop_column("appointment", "duration_min")
    op.drop_constraint("fk_appointment_service_id", "appointment", type_="foreignkey")
    op.drop_column("appointment", "service_id")
    op.drop_column("clinic", "timezone")
