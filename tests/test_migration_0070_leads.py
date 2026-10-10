"""Тесты миграции 0070: таблица ``lead`` переезжает в ``clinic_application`` (и обратно).

Обычные API-тесты гоняют миграции один раз и на пустой базе, а эта миграция
**переносит данные** и удаляет таблицу: ошибка в ней видна только на живых строках.
Поэтому здесь поднимается отдельный Postgres, накатывается схема 0069, засеиваются
лиды и заявки через OTP, и только потом запускается 0070 — как на проде.

Ключевые риски:

* лид пропал или потерял id/дату при переносе (команда не увидит заявку);
* свободный текст ``doctors_count_range`` попал в enum не по правилу
  «ровно ``2-5`` | ``6-10`` | ``10+`` после trim» (или упал на ``'  6-10 '``);
* прежние заявки через OTP «потеряли» подтверждение телефона;
* ограничения (CHECK-и и частичный уникальный индекс) не встали или не держат;
* ``downgrade`` падает на базе с данными ОБЕИХ дверей (дубли телефона, лид без
  названия клиники — в ``lead`` оно NOT NULL, телефон там UNIQUE).

Базы изолированы по модулям фикстур (отдельный ``CREATE DATABASE`` в одном
контейнере): тесты, читающие результат, не мешают тем, что мутируют схему.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import _ensure_docker_env

try:  # как в conftest: без Docker тесты пропускаются мягко, а не падают импортом
    from testcontainers.postgres import PostgresContainer
except Exception:  # pragma: no cover
    PostgresContainer = None

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PREV = "0069_subscription_plans"
_REV = "0070_leads_into_applications"

CLINIC = "11111111-0000-0000-0000-0000000000c1"
# Лиды таблицы lead (схема 0069): по одному на каждый случай разбора doctors_count_range.
L_2_5 = "aaaaaaaa-0000-0000-0000-000000000001"
L_PADDED = "aaaaaaaa-0000-0000-0000-000000000002"
L_10_PLUS = "aaaaaaaa-0000-0000-0000-000000000003"
L_JUNK = "aaaaaaaa-0000-0000-0000-000000000004"
L_NULL = "aaaaaaaa-0000-0000-0000-000000000005"
L_EMPTY = "aaaaaaaa-0000-0000-0000-000000000006"
LEAD_IDS = [L_2_5, L_PADDED, L_10_PLUS, L_JUNK, L_NULL, L_EMPTY]
# Заявки через OTP (дверь №1) — до миграции единственные в clinic_application.
A_PENDING = "bbbbbbbb-0000-0000-0000-000000000001"
A_REPEAT = "bbbbbbbb-0000-0000-0000-000000000002"
A_REJECTED = "bbbbbbbb-0000-0000-0000-000000000003"
OTP_IDS = [A_PENDING, A_REPEAT, A_REJECTED]
# Строки «новой двери», добавленные ПОСЛЕ 0070 (для проверки downgrade).
N_NO_CLINIC = "cccccccc-0000-0000-0000-000000000001"
N_DUP_EARLY = "cccccccc-0000-0000-0000-000000000002"
N_DUP_LATE = "cccccccc-0000-0000-0000-000000000003"
N_CONVERTED = "cccccccc-0000-0000-0000-000000000004"
N_SAME_PHONE_AS_LEAD = "cccccccc-0000-0000-0000-000000000005"

_SEED_0069 = f"""
INSERT INTO clinic (id, name, type, lang) VALUES ('{CLINIC}', 'Конвертированная', 'clinic', 'ru');

INSERT INTO lead (id, full_name, phone, clinic_name, doctors_count_range, comment,
                  converted_clinic_id, created_at) VALUES
 ('{L_2_5}', 'Лид Два-Пять', '+996700000001', 'Клиника А', '2-5', 'коммент 1',
  '{CLINIC}', '2026-01-10 10:00:00+00'),
 ('{L_PADDED}', 'Лид С Пробелами', '+996700000002', 'Клиника Б', '  6-10 ', NULL,
  NULL, '2026-02-10 10:00:00+00'),
 ('{L_10_PLUS}', 'Лид Десять Плюс', '+996700000003', 'Клиника В', '10+', 'коммент 3',
  NULL, '2026-03-10 10:00:00+00'),
 ('{L_JUNK}', 'Лид Мусор', '+996700000004', 'Клиника Г', '2-3', NULL,
  NULL, '2026-04-10 10:00:00+00'),
 ('{L_NULL}', 'Лид Без Диапазона', '+996700000005', 'Клиника Д', NULL, NULL,
  NULL, '2026-05-10 10:00:00+00'),
 ('{L_EMPTY}', 'Лид Пустой Диапазон', '+996700000006', 'Клиника Е', '', NULL,
  NULL, '2026-05-11 10:00:00+00');

-- Заявки через OTP: A_REPEAT повторяет телефон A_PENDING, A_PENDING — телефон лида L_2_5.
INSERT INTO clinic_application (id, company_name, city_id, doctors_count, phone, status,
                                reviewed_at, created_at, updated_at)
SELECT '{A_PENDING}', 'Заявка 1', id, '6-10', '+996700000001', 'pending',
       NULL, '2026-06-01 10:00:00+00', '2026-06-01 10:00:00+00' FROM city LIMIT 1;
INSERT INTO clinic_application (id, company_name, city_id, doctors_count, phone, status,
                                reviewed_at, created_at, updated_at)
SELECT '{A_REPEAT}', 'Заявка 1 (повтор)', id, '2-5', '+996700000001', 'pending',
       NULL, '2026-06-02 10:00:00+00', '2026-06-02 10:00:00+00' FROM city LIMIT 1;
INSERT INTO clinic_application (id, company_name, city_id, doctors_count, phone, status,
                                reviewed_at, created_at, updated_at)
SELECT '{A_REJECTED}', 'Заявка 3', id, '10+', '+996700000010', 'rejected',
       '2026-06-04 10:00:00+00', '2026-06-03 10:00:00+00', '2026-06-04 10:00:00+00'
FROM city LIMIT 1;
"""

# Новая дверь (после 0070): лид без названия клиники, дубль телефона у лидов,
# конвертированный лид и лид с телефоном, который уже был у перенесённого.
_SEED_NEW_DOOR = f"""
INSERT INTO clinic_application (id, full_name, phone, phone_verified, status, source,
                                company_name, doctors_count, comment, converted_clinic_id,
                                created_at, updated_at) VALUES
 ('{N_NO_CLINIC}', 'Лид Без Клиники', '+996700000101', false, 'pending', 'instagram',
  NULL, NULL, NULL, NULL, '2026-06-10 10:00:00+00', '2026-06-10 10:00:00+00'),
 ('{N_DUP_EARLY}', 'Дубль Ранний', '+996700000102', false, 'reviewed', 'google',
  'Ранняя', '2-5', 'ранний комментарий', NULL,
  '2026-06-11 10:00:00+00', '2026-06-11 10:00:00+00'),
 ('{N_DUP_LATE}', 'Дубль Поздний', '+996700000102', false, 'pending', NULL,
  'Поздняя', '10+', NULL, NULL, '2026-06-12 10:00:00+00', '2026-06-12 10:00:00+00'),
 ('{N_CONVERTED}', 'Лид Конвертированный', '+996700000103', false, 'reviewed', NULL,
  'Конверт', '6-10', NULL, '{CLINIC}',
  '2026-06-13 10:00:00+00', '2026-06-13 10:00:00+00'),
 ('{N_SAME_PHONE_AS_LEAD}', 'Повтор Телефона Лида', '+996700000002', false, 'reviewed',
  NULL, NULL, NULL, NULL, NULL, '2026-06-14 10:00:00+00', '2026-06-14 10:00:00+00')
"""


# ── Инфраструктура ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migration_pg() -> Iterator[str]:
    """Отдельный Postgres под миграционные тесты; отдаёт URL администратора (БД ``test``)."""
    if PostgresContainer is None:
        pytest.skip("testcontainers/Docker недоступны")
    _ensure_docker_env()
    with PostgresContainer(
        "postgres:16-alpine", username="test", password="test", dbname="test"
    ) as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql+asyncpg://test:test@{host}:{port}/test"


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Запустить alembic отдельным процессом — как это делает деплой."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


def _must(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]


async def _sql(url: str, statements: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for statement in filter(None, (s.strip() for s in statements.split(";"))):
            await conn.execute(text(statement))
    await engine.dispose()


async def _fetch(url: str, query: str) -> list[tuple[Any, ...]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (await conn.execute(text(query))).all()
    await engine.dispose()
    return [tuple(r) for r in rows]


async def _probe(url: str, statements: list[str], query: str) -> list[tuple[Any, ...]]:
    """Выполнить вставки и запрос в транзакции, которая ОТКАТЫВАЕТСЯ — общая БД не меняется."""
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            for statement in statements:
                await conn.execute(text(statement))
            rows = (await conn.execute(text(query))).all()
        finally:
            await transaction.rollback()
    await engine.dispose()
    return [tuple(r) for r in rows]


async def _new_database(admin_url: str, name: str) -> str:
    """Создать пустую БД в контейнере и вернуть URL на неё."""
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


async def _build(admin_url: str, name: str) -> str:
    """Свежая БД на схеме 0069 с засеянными лидами и заявками через OTP."""
    url = await _new_database(admin_url, name)
    _must(_alembic(url, "upgrade", _PREV))
    await _sql(url, _SEED_0069)
    return url


@pytest_asyncio.fixture(scope="module")
async def upgraded(migration_pg: str) -> AsyncIterator[str]:
    """БД после ``upgrade 0070`` поверх засеянной 0069 — для тестов, которые только читают."""
    url = await _build(migration_pg, "mig_upgraded")
    _must(_alembic(url, "upgrade", _REV))
    yield url


@pytest_asyncio.fixture(scope="module")
async def downgraded(migration_pg: str) -> AsyncIterator[str]:
    """БД с данными обеих дверей, откаченная ``downgrade`` до 0069 — тесты только читают."""
    url = await _build(migration_pg, "mig_downgraded")
    _must(_alembic(url, "upgrade", _REV))
    await _sql(url, _SEED_NEW_DOOR)
    _must(_alembic(url, "downgrade", _PREV))
    yield url


# ── upgrade: перенос данных ─────────────────────────────────────────────────


async def test_upgrade_moves_every_lead_into_the_queue_as_pending_and_unverified(
    upgraded: str,
) -> None:
    """Каждый лид стал заявкой: тот же id и дата, ``pending``, телефон не подтверждён."""
    rows = await _fetch(
        upgraded,
        "SELECT id::text, full_name, phone, company_name, comment, converted_clinic_id::text, "
        "phone_verified, status::text, source, city_id, created_at, updated_at, "
        "reviewed_by_account_id, reviewed_at "
        f"FROM clinic_application WHERE id IN ({', '.join(repr(i) for i in LEAD_IDS)}) "
        "ORDER BY created_at",
    )

    assert [r[0] for r in rows] == LEAD_IDS  # id сохранены, потерянных нет
    first = rows[0]
    assert first[1:6] == ("Лид Два-Пять", "+996700000001", "Клиника А", "коммент 1", CLINIC)
    for row in rows:
        _, _, _, company, _, _, verified, status, source, city, created, updated, rev_by, rev_at = (
            row
        )
        assert company, "clinic_name должен стать company_name"
        assert verified is False
        assert status == "pending"  # их никто не разбирал: панели не было
        assert source is None
        assert city is None
        assert created == updated  # updated_at = created_at лида
        assert rev_by is None
        assert rev_at is None
    assert rows[0][10] == datetime.fromisoformat("2026-01-10T10:00:00+00:00")
    assert [r[5] for r in rows[1:]] == [None] * 5  # конверсия перенесена только у первого


async def test_upgrade_maps_doctors_count_range_only_for_exact_values(upgraded: str) -> None:
    """Текст → enum только при точном совпадении после trim; остальное — NULL (не падение)."""
    rows = dict(
        await _fetch(
            upgraded,
            "SELECT id::text, doctors_count::text FROM clinic_application "
            f"WHERE id IN ({', '.join(repr(i) for i in LEAD_IDS)})",
        )
    )

    assert rows[L_2_5] == "2-5"
    assert rows[L_PADDED] == "6-10"  # '  6-10 ' → trim → 6-10
    assert rows[L_10_PLUS] == "10+"
    assert rows[L_JUNK] is None  # '2-3' — свой диапазон, не угадываем
    assert rows[L_NULL] is None
    assert rows[L_EMPTY] is None


async def test_upgrade_keeps_otp_applications_verified_and_untouched(upgraded: str) -> None:
    """Прежние заявки через OTP остались как были и помечены подтверждёнными."""
    rows = await _fetch(
        upgraded,
        "SELECT id::text, company_name, doctors_count::text, phone, status::text, "
        "phone_verified, full_name, source, comment, converted_clinic_id, city_id IS NOT NULL "
        f"FROM clinic_application WHERE id IN ({', '.join(repr(i) for i in OTP_IDS)}) "
        "ORDER BY created_at",
    )

    assert rows == [
        (
            A_PENDING,
            "Заявка 1",
            "6-10",
            "+996700000001",
            "pending",
            True,
            None,
            None,
            None,
            None,
            True,
        ),
        (
            A_REPEAT,
            "Заявка 1 (повтор)",
            "2-5",
            "+996700000001",  # повтор телефона среди заявок через OTP по-прежнему допустим
            "pending",
            True,
            None,
            None,
            None,
            None,
            True,
        ),
        (
            A_REJECTED,
            "Заявка 3",
            "10+",
            "+996700000010",
            "rejected",
            True,
            None,
            None,
            None,
            None,
            True,
        ),
    ]
    total = await _fetch(upgraded, "SELECT count(*) FROM clinic_application")
    assert total == [(len(LEAD_IDS) + len(OTP_IDS),)]


async def test_upgrade_drops_lead_table(upgraded: str) -> None:
    """Таблицы ``lead`` больше нет — ни данных, ни ограничений."""
    assert await _fetch(upgraded, "SELECT to_regclass('public.lead')::text") == [(None,)]
    constraints = await _fetch(
        upgraded,
        "SELECT conname FROM pg_constraint "
        "WHERE conname IN ('lead_phone_key', 'fk_lead_converted_clinic')",
    )
    assert constraints == []


# ── upgrade: форма схемы ────────────────────────────────────────────────────


async def test_upgrade_shapes_the_schema(upgraded: str) -> None:
    """Nullable-колонки, ``phone_verified`` NOT NULL без default, FK конверсии — SET NULL."""
    columns = {
        r[0]: r[1:]
        for r in await _fetch(
            upgraded,
            "SELECT column_name, is_nullable, column_default, data_type "
            "FROM information_schema.columns WHERE table_name = 'clinic_application'",
        )
    }

    for name in ("company_name", "city_id", "doctors_count", "full_name", "source", "comment"):
        assert columns[name][0] == "YES", f"{name} должен быть nullable"
    assert columns["converted_clinic_id"][0] == "YES"
    # Признак двери обязателен и задаётся явно: server_default после бэкфилла снят.
    assert columns["phone_verified"][0] == "NO"
    assert columns["phone_verified"][1] is None
    assert columns["phone_verified"][2] == "boolean"
    assert columns["phone"][0] == "NO"  # телефон обязателен у любой двери

    fk = await _fetch(
        upgraded,
        "SELECT confdeltype::text, confrelid::regclass::text FROM pg_constraint "
        "WHERE conname = 'fk_clinic_application_converted_clinic'",
    )
    assert fk == [("n", "clinic")]  # ON DELETE SET NULL


async def test_upgrade_creates_both_checks_and_the_partial_unique_index(upgraded: str) -> None:
    """Оба CHECK-а и частичный уникальный индекс на месте и с нужным условием."""
    checks = dict(
        await _fetch(
            upgraded,
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'clinic_application'::regclass AND contype = 'c'",
        )
    )
    assert set(checks) == {
        "ck_clinic_application_verified_complete",
        "ck_clinic_application_lead_named",
    }

    (index,) = await _fetch(
        upgraded,
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'ux_clinic_application_pending_lead_phone'",
    )
    definition = index[0]
    assert "UNIQUE" in definition
    assert "(phone)" in definition
    assert "'pending'" in definition
    assert "phone_verified = false" in definition or "NOT phone_verified" in definition


async def _expect_violation(url: str, statement: str, constraint: str) -> None:
    """Вставка нарушает именно это ограничение (транзакция откатывается сама)."""
    with pytest.raises(Exception, match=constraint):
        await _sql(url, statement)


_LEAD = (
    "INSERT INTO clinic_application (id, phone, phone_verified, status, full_name) "
    "VALUES (gen_random_uuid(), '{phone}', false, '{status}', {name})"
)


async def test_constraints_reject_incomplete_verified_application(upgraded: str) -> None:
    """Заявка с подтверждённым телефоном без компании/города/размера — нарушение CHECK."""
    await _expect_violation(
        upgraded,
        "INSERT INTO clinic_application (id, phone, phone_verified, status) "
        "VALUES (gen_random_uuid(), '+996700009001', true, 'pending')",
        "ck_clinic_application_verified_complete",
    )


async def test_constraints_reject_nameless_lead(upgraded: str) -> None:
    """Лид без имени — строка из одного номера — нарушение CHECK."""
    await _expect_violation(
        upgraded,
        _LEAD.format(phone="+996700009002", status="pending", name="NULL"),
        "ck_clinic_application_lead_named",
    )


async def test_partial_unique_index_rejects_second_open_lead_on_the_same_phone(
    upgraded: str,
) -> None:
    """Два открытых лида на один номер — нарушение индекса (замок от гонки двух отправок)."""
    phone = "+996700009003"
    engine = create_async_engine(upgraded)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            await conn.execute(text(_LEAD.format(phone=phone, status="pending", name="'Первый'")))
            with pytest.raises(Exception, match="ux_clinic_application_pending_lead_phone"):
                await conn.execute(
                    text(_LEAD.format(phone=phone, status="pending", name="'Второй'"))
                )
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_partial_unique_index_does_not_limit_resolved_leads_or_otp_applications(
    upgraded: str,
) -> None:
    """Разобранные лиды и заявки через OTP на тот же номер индексом не ограничены.

    Один открытый лид + сколько угодно reviewed/rejected + повторные заявки через OTP —
    всё это допустимо: человек вправе прийти снова, а компания — подать повторно.
    """
    phone = "+996700009004"
    otp = (
        "INSERT INTO clinic_application (id, phone, phone_verified, status, company_name, "
        f"city_id, doctors_count) SELECT gen_random_uuid(), '{phone}', true, 'pending', "
        "'Компания', id, '2-5' FROM city LIMIT 1"
    )
    rows = await _probe(
        upgraded,
        [
            _LEAD.format(phone=phone, status="pending", name="'Открытый'"),
            _LEAD.format(phone=phone, status="reviewed", name="'Разобранный'"),
            _LEAD.format(phone=phone, status="rejected", name="'Отклонённый'"),
            otp,
            otp,
        ],
        "SELECT phone_verified, status::text, count(*) FROM clinic_application "
        f"WHERE phone = '{phone}' GROUP BY 1, 2 ORDER BY 1, 2",
    )

    assert rows == [
        (False, "pending", 1),
        (False, "rejected", 1),
        (False, "reviewed", 1),
        (True, "pending", 2),
    ]
    # Проба откатилась: общая БД не изменилась.
    total = await _fetch(upgraded, "SELECT count(*) FROM clinic_application")
    assert total == [(len(LEAD_IDS) + len(OTP_IDS),)]


# ── downgrade: данные обеих дверей ──────────────────────────────────────────


async def test_downgrade_with_both_doors_returns_unverified_to_lead_table(downgraded: str) -> None:
    """Откат на базе с данными обеих дверей прошёл; неподтверждённые заявки вернулись в ``lead``.

    ``lead.phone`` уникален, а в очереди номер мог повторяться — остаётся самая
    ранняя; ``clinic_name`` в ``lead`` обязателен — без названия берётся имя контакта.
    """
    rows = {
        r[0]: r[1:]
        for r in await _fetch(
            downgraded,
            "SELECT id::text, full_name, phone, clinic_name, doctors_count_range, comment, "
            "converted_clinic_id::text, created_at FROM lead",
        )
    }

    # 6 исходных лидов + 3 новых; поздний дубль и повтор телефона перенесённого лида — нет.
    assert set(rows) == {*LEAD_IDS, N_NO_CLINIC, N_DUP_EARLY, N_CONVERTED}
    assert N_DUP_LATE not in rows
    assert N_SAME_PHONE_AS_LEAD not in rows

    assert rows[L_2_5] == (
        "Лид Два-Пять",
        "+996700000001",
        "Клиника А",
        "2-5",
        "коммент 1",
        CLINIC,
        datetime.fromisoformat("2026-01-10T10:00:00+00:00"),
    )
    assert rows[L_PADDED][3] == "6-10"  # уже нормализовано апгрейдом
    assert rows[L_JUNK][3] is None  # распознать нечего: NULL
    # Лид без названия клиники: NOT NULL в lead.clinic_name → имя контакта.
    assert rows[N_NO_CLINIC][2:4] == ("Лид Без Клиники", None)
    assert rows[N_NO_CLINIC][1] == "+996700000101"
    # Из дублей телефона — самый ранний (reviewed); его данные, а не позднего.
    assert rows[N_DUP_EARLY][2:5] == ("Ранняя", "2-5", "ранний комментарий")
    # Конверсия возвращается.
    assert rows[N_CONVERTED][5] == CLINIC


async def test_downgrade_leaves_only_verified_applications_and_old_columns(
    downgraded: str,
) -> None:
    """В ``clinic_application`` остаются только заявки через OTP; схема как до 0070."""
    remaining = await _fetch(
        downgraded, "SELECT id::text FROM clinic_application ORDER BY created_at"
    )
    assert [r[0] for r in remaining] == OTP_IDS

    columns = {
        r[0]: r[1]
        for r in await _fetch(
            downgraded,
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'clinic_application'",
        )
    }
    for gone in ("full_name", "phone_verified", "source", "comment", "converted_clinic_id"):
        assert gone not in columns, f"{gone} должна исчезнуть при откате"
    for restored in ("company_name", "city_id", "doctors_count"):
        assert columns[restored] == "NO", f"{restored} снова NOT NULL"

    leftovers = await _fetch(
        downgraded,
        "SELECT conname FROM pg_constraint WHERE conname IN "
        "('ck_clinic_application_verified_complete', 'ck_clinic_application_lead_named', "
        "'fk_clinic_application_converted_clinic')",
    )
    assert leftovers == []
    assert (
        await _fetch(
            downgraded,
            "SELECT 1 FROM pg_indexes WHERE indexname = 'ux_clinic_application_pending_lead_phone'",
        )
        == []
    )
    # Прежние индексы 0023 не пострадали.
    kept = await _fetch(
        downgraded,
        "SELECT indexname FROM pg_indexes WHERE tablename = 'clinic_application' "
        "AND indexname LIKE 'ix_clinic_application_%' ORDER BY 1",
    )
    assert kept == [("ix_clinic_application_phone",), ("ix_clinic_application_status_created",)]


async def test_downgrade_restores_lead_table_as_in_0008(downgraded: str) -> None:
    """Таблица ``lead`` после отката — как в 0008: типы, NOT NULL, уникальный телефон, FK."""
    columns = {
        r[0]: r[1:]
        for r in await _fetch(
            downgraded,
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = 'lead'",
        )
    }
    assert {k: v[:2] for k, v in columns.items()} == {
        "id": ("uuid", "NO"),
        "full_name": ("text", "NO"),
        "phone": ("text", "NO"),
        "clinic_name": ("text", "NO"),
        "doctors_count_range": ("text", "YES"),
        "comment": ("text", "YES"),
        "converted_clinic_id": ("uuid", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
    }
    assert "now()" in columns["created_at"][2]

    constraints = {
        r[0]: r[1]
        for r in await _fetch(
            downgraded,
            "SELECT conname, contype::text FROM pg_constraint WHERE conrelid = 'lead'::regclass",
        )
    }
    assert constraints == {"lead_pkey": "p", "lead_phone_key": "u", "fk_lead_converted_clinic": "f"}


# ── Круг: upgrade → данные новой двери → downgrade → upgrade ───────────────


async def test_upgrade_again_after_downgrade_brings_leads_back_to_the_queue(
    migration_pg: str,
) -> None:
    """Полный круг на данных обеих дверей: после повторного ``upgrade`` лиды снова в очереди.

    Статус рассмотрения и source при откате теряются (в ``lead`` им некуда деться) —
    вернувшиеся лиды снова ``pending`` и неподтверждённые; заявки через OTP — как были.
    """
    url = await _build(migration_pg, "mig_roundtrip")
    _must(_alembic(url, "upgrade", _REV))
    await _sql(url, _SEED_NEW_DOOR)
    _must(_alembic(url, "downgrade", _PREV))
    _must(_alembic(url, "upgrade", _REV))

    rows = await _fetch(
        url,
        "SELECT id::text, phone_verified, status::text, source FROM clinic_application "
        "ORDER BY created_at",
    )
    by_id = {r[0]: r[1:] for r in rows}

    expected_leads = {*LEAD_IDS, N_NO_CLINIC, N_DUP_EARLY, N_CONVERTED}
    assert set(by_id) == expected_leads | set(OTP_IDS)
    for lead_id in expected_leads:
        assert by_id[lead_id] == (False, "pending", None), lead_id
    for otp_id in OTP_IDS:
        assert by_id[otp_id][0] is True
    assert by_id[A_REJECTED][1] == "rejected"  # статус заявки через OTP пережил круг
    assert await _fetch(url, "SELECT to_regclass('public.lead')::text") == [(None,)]


async def test_downgrade_on_database_without_leads_keeps_all_applications(
    migration_pg: str,
) -> None:
    """Если в очереди только заявки через OTP, откат ничего не теряет и проходит."""
    url = await _build(migration_pg, "mig_otp_only")
    await _sql(url, "DELETE FROM lead")
    _must(_alembic(url, "upgrade", _REV))

    _must(_alembic(url, "downgrade", _PREV))

    assert await _fetch(url, "SELECT count(*) FROM lead") == [(0,)]
    remaining = await _fetch(url, "SELECT id::text FROM clinic_application ORDER BY created_at")
    assert [r[0] for r in remaining] == OTP_IDS
