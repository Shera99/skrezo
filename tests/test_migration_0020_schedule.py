"""Тесты миграции 0020 на подготовленных данных схемы 0019.

Обычные API-тесты гоняют миграции ровно один раз и на пустой базе, поэтому целый
класс дефектов им не виден: миграция может не встать на **живых** данных. Ровно так
и вышло — 0020 задним числом выдаёт всей истории ``duration_min = 30`` и объявляет
«приёмы врача не пересекаются», а до неё запись врача на 09:00 и 09:10 была законной.

Здесь поднимается отдельный Postgres (схему между тестами пересоздаём — общий
контейнер из ``conftest`` для этого не годится), накатывается 0019, засеваются
данные и запускается ``alembic upgrade head``.

Ключевое: дефект возникает только на **паре** строк. На одной строке пересечься
не с чем — поэтому единичных случаев здесь нет нигде.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

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
BISHKEK = ZoneInfo("Asia/Bishkek")
_SCHEMA_0019 = "0019_clinical_core_v2"
_HEAD = "0020_schedule"

CLINIC = "11111111-0000-0000-0000-000000000001"
PATIENT = "22222222-0000-0000-0000-000000000001"
DOCTOR_1 = "33333333-0000-0000-0000-000000000001"
DOCTOR_2 = "33333333-0000-0000-0000-000000000002"

# Клиника, пациент и два врача — общий фон для всех сценариев.
_SEED_BASE = f"""
INSERT INTO clinic (id, name, type, lang) VALUES ('{CLINIC}', 'Легаси', 'clinic', 'ru');
INSERT INTO patient (id, phone, first_name) VALUES ('{PATIENT}', '+996700000101', 'Пациент');
INSERT INTO account (id, phone, full_name)
VALUES ('44444444-0000-0000-0000-000000000001', '+996700000102', 'Врач Первый');
INSERT INTO account (id, phone, full_name)
VALUES ('44444444-0000-0000-0000-000000000002', '+996700000103', 'Врач Второй');
INSERT INTO membership (id, clinic_id, account_id, role_id)
SELECT '{DOCTOR_1}', '{CLINIC}', '44444444-0000-0000-0000-000000000001', id FROM role LIMIT 1;
INSERT INTO membership (id, clinic_id, account_id, role_id)
SELECT '{DOCTOR_2}', '{CLINIC}', '44444444-0000-0000-0000-000000000002', id FROM role LIMIT 1;
"""


@pytest.fixture(scope="module")
def legacy_pg() -> Iterator[str]:
    """Отдельный Postgres под миграционные тесты (схема здесь пересоздаётся)."""
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
    """Запустить alembic как отдельный процесс — как это делает деплой."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


async def _sql(url: str, statements: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for statement in filter(None, (s.strip() for s in statements.split(";"))):
            await conn.execute(text(statement))
    await engine.dispose()


async def _fetch(url: str, query: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (await conn.execute(text(query))).all()
    await engine.dispose()
    return [tuple(r) for r in rows]


@pytest_asyncio.fixture
async def at_0019(legacy_pg: str) -> AsyncIterator[str]:
    """Чистая схема 0019 с базовым сидом — состояние ДО миграции расписания."""
    engine = create_async_engine(legacy_pg, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    result = _alembic(legacy_pg, "upgrade", _SCHEMA_0019)
    assert result.returncode == 0, result.stderr
    await _sql(legacy_pg, _SEED_BASE)
    yield legacy_pg


def _appointment(
    *,
    doctor: str | None,
    scheduled_at: str | None,
    status: str = "scheduled",
    created_at: str | None = None,
) -> str:
    """INSERT приёма на схеме 0019 (там ещё нет ни duration_min, ни констрейнта)."""
    doctor_sql = f"'{doctor}'" if doctor else "NULL"
    scheduled_sql = f"'{scheduled_at}'" if scheduled_at else "NULL"
    created_sql = f"'{created_at}'" if created_at else ("NULL" if scheduled_at is None else "now()")
    return (
        "INSERT INTO appointment (id, clinic_id, patient_id, doctor_membership_id, "
        "appointment_type, status, price_total, scheduled_at, created_at, updated_at) "
        f"VALUES ('{uuid.uuid4()}', '{CLINIC}', '{PATIENT}', {doctor_sql}, "
        f"'treatment', '{status}', 0, {scheduled_sql}, {created_sql}, now())"
    )


async def _assert_rolled_back_to_0019(url: str) -> None:
    """Откат чистый: версия 0019 и колонки duration_min ещё нет."""
    current = _alembic(url, "current")
    assert _SCHEMA_0019 in current.stdout, current.stdout
    assert _HEAD not in current.stdout, current.stdout

    columns = await _fetch(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'appointment' AND column_name = 'duration_min'",
    )
    assert columns == [], "duration_min не должен остаться после отката"


# ── Блокер: пара пересекающихся легаси-приёмов ──────────────────────────────


async def test_upgrade_stops_with_preflight_message_on_overlapping_legacy_pair(
    at_0019: str,
) -> None:
    """Пара 09:00 + 09:10 у одного врача → миграция падает pre-flight'ом, а не сырой ошибкой БД.

    До 0020 это законные данные: длительности у приёма не было, пересечения никто
    не запрещал. Бэкфилл duration_min = 30 делает их пересечением задним числом.
    """
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:10:00+06"),
    )

    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode != 0, "миграция обязана остановиться на таких данных"

    output = result.stdout + result.stderr
    # Падение осмысленное: наше сообщение, а не сырой ExclusionViolationError.
    assert "Миграция 0020 остановлена" in output, output[-3000:]
    assert "ex_appointment_doctor_overlap" in output
    assert "Найдено пересекающихся пар: 1" in output, output[-3000:]
    assert "ExclusionViolationError" not in output, "человек не должен видеть сырую ошибку БД"

    await _assert_rolled_back_to_0019(at_0019)

    # Данные не тронуты: обе строки на месте и со своим временем.
    rows = await _fetch(
        at_0019,
        "SELECT to_char(scheduled_at AT TIME ZONE 'Asia/Bishkek', 'HH24:MI') "
        "FROM appointment ORDER BY scheduled_at",
    )
    assert rows == [("09:00",), ("09:10",)]


async def test_upgrade_stops_when_backfill_itself_creates_the_overlap(at_0019: str) -> None:
    """Второй путь: scheduled_at IS NULL → COALESCE(created_at, now()) разводит их на 10 минут.

    Pre-flight стоит ПОСЛЕ бэкфилла, поэтому обязан видеть уже подставленное время,
    а не исходные NULL.
    """
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at=None, created_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_1, scheduled_at=None, created_at="2026-07-20 09:10:00+06"),
    )

    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode != 0, "пересечение, созданное бэкфиллом, обязано ловиться"

    output = result.stdout + result.stderr
    assert "Миграция 0020 остановлена" in output, output[-3000:]
    assert "Найдено пересекающихся пар: 1" in output, output[-3000:]

    await _assert_rolled_back_to_0019(at_0019)
    # Исходные NULL не переписаны — миграция откачена целиком.
    nulls = await _fetch(at_0019, "SELECT count(*) FROM appointment WHERE scheduled_at IS NULL")
    assert nulls == [(2,)]


# ── Негативные контрольные: pre-flight не должен быть жадным ────────────────


async def test_upgrade_passes_for_back_to_back_legacy_pair(at_0019: str) -> None:
    """Приёмы встык (09:00 и 09:30 при duration 30) — не пересечение, миграция проходит."""
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:30:00+06"),
    )

    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]

    rows = await _fetch(at_0019, "SELECT duration_min FROM appointment ORDER BY scheduled_at")
    assert rows == [(30,), (30,)], "бэкфилл длительности должен отработать"


async def test_upgrade_passes_when_one_of_overlapping_pair_is_cancelled(at_0019: str) -> None:
    """Отменённый приём под констрейнт не попадает — pre-flight обязан его игнорировать."""
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:10:00+06", status="cancelled"),
    )

    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]


async def test_upgrade_passes_for_overlapping_pair_without_doctor(at_0019: str) -> None:
    """Приёмы без врача слот не занимают — констрейнт их не покрывает."""
    await _sql(
        at_0019,
        _appointment(doctor=None, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=None, scheduled_at="2026-07-20 09:10:00+06"),
    )

    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]


async def test_upgrade_passes_for_same_interval_of_different_doctors(at_0019: str) -> None:
    """Один интервал у РАЗНЫХ врачей — не пересечение."""
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_2, scheduled_at="2026-07-20 09:00:00+06"),
    )

    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]


async def test_upgrade_passes_on_clean_database(at_0019: str) -> None:
    """Регресс: без приёмов миграция встаёт как раньше, констрейнт на месте."""
    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]

    constraint = await _fetch(
        at_0019,
        "SELECT conname FROM pg_constraint WHERE conname = 'ex_appointment_doctor_overlap'",
    )
    assert constraint == [("ex_appointment_doctor_overlap",)]


async def test_constraint_is_live_after_upgrade_of_legacy_data(at_0019: str) -> None:
    """После успешной миграции констрейнт реально защищает — а не просто «создался»."""
    await _sql(at_0019, _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06"))
    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]

    with pytest.raises(Exception, match="ex_appointment_doctor_overlap"):
        await _sql(
            at_0019,
            "INSERT INTO appointment (id, clinic_id, patient_id, doctor_membership_id, "
            "appointment_type, status, price_total_tiyin, scheduled_at, duration_min) "
            f"VALUES (gen_random_uuid(), '{CLINIC}', '{PATIENT}', '{DOCTOR_1}', "
            "'treatment', 'scheduled', 0, '2026-07-20 09:10:00+06', 30)",
        )


# ── Сообщение об ошибке как рабочий инструмент, а не украшение ──────────────


_SQL_MARKER = "Показать конфликты (запрос рассчитан на текущую схему):"


def _sql_from_message(output: str) -> str:
    """Вытащить диагностический запрос из сообщения — как это сделал бы человек."""
    assert _SQL_MARKER in output, output[-3000:]
    tail = output[output.find(_SQL_MARKER) + len(_SQL_MARKER) :]
    return tail[: tail.find(";") + 1].strip()


async def test_preflight_message_sql_runs_on_the_schema_operator_actually_has(
    at_0019: str,
) -> None:
    """Запрос из сообщения обязан выполняться ПОСЛЕ отката, копипастой, без правок.

    DDL транзакционный: миграция откатывается целиком, и человек читает сообщение
    уже на схеме 0019 — где ни duration_min, ни appointment_tstzrange() не существует.
    Запрос, ссылающийся на них, упал бы «column a.duration_min does not exist»,
    то есть сообщение отправляло бы к нерабочему инструменту.
    """
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:10:00+06"),
    )
    result = _alembic(at_0019, "upgrade", "head")
    assert result.returncode != 0

    query = _sql_from_message(result.stdout + result.stderr)
    # Запрос не должен опираться на то, чего после отката нет.
    assert "duration_min" not in query, query
    assert "appointment_tstzrange" not in query, query

    # Главное — он реально отрабатывает на откаченной схеме и называет ту самую пару.
    rows = await _fetch(at_0019, query)
    assert len(rows) == 1, rows
    _, _, doctor, first_at, second_at = rows[0]
    assert str(doctor) == DOCTOR_1
    # Внутри пары запрос упорядочивает строки по id (b.id > a.id), а не по времени,
    # поэтому сравниваем как множество — иначе тест флакал бы на случайных UUID.
    times = sorted(
        cast(datetime, moment).astimezone(BISHKEK).strftime("%H:%M")
        for moment in (first_at, second_at)
    )
    assert times == ["09:00", "09:10"], f"запрос должен указать ту самую пару: {rows}"


async def test_preflight_advice_actually_unblocks_the_migration(at_0019: str) -> None:
    """Совет из сообщения работает: отменяем лишний приём → миграция проходит.

    Сообщение бесполезно, если по нему нельзя починить базу. Здесь проходится
    ровно тот путь, который проходит дежурный: упали → прочитали → применили → накатили.
    """
    await _sql(
        at_0019,
        _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:00:00+06")
        + ";"
        + _appointment(doctor=DOCTOR_1, scheduled_at="2026-07-20 09:10:00+06"),
    )
    failed = _alembic(at_0019, "upgrade", "head")
    assert failed.returncode != 0

    # Совет №2 из сообщения: отменить лишний приём (доступно на схеме 0019).
    conflicting = await _fetch(at_0019, _sql_from_message(failed.stdout + failed.stderr))
    await _sql(
        at_0019, f"UPDATE appointment SET status = 'cancelled' WHERE id = '{conflicting[0][1]}'"
    )

    fixed = _alembic(at_0019, "upgrade", "head")
    assert fixed.returncode == 0, (fixed.stdout + fixed.stderr)[-3000:]
