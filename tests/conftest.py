"""Фикстуры интеграционных API-тестов.

Поднимает эфемерный Postgres (testcontainers), прогоняет на нём реальные миграции
Alembic (схема + сиды справочников/ролей), подменяет зависимости приложения
``get_session`` (тестовый движок) и ``get_redis`` (fakeredis) и отдаёт httpx-клиент
поверх ASGI. Между тестами мутируемые таблицы чистятся, справочники/шаблоны — нет.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fakeredis import aioredis
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import hash_password
from app.main import app
from app.shared.db.enums import Lang
from app.shared.db.models import Account
from app.shared.domain.value_objects import normalize_phone

try:  # testcontainers импортируется лениво — тесты вне Docker пропускаются мягче
    from testcontainers.postgres import PostgresContainer
except Exception:  # pragma: no cover
    PostgresContainer = None

API = "/api/v1"
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Часы клиники по умолчанию: пн–сб 09:00–18:00, обед 12:00–13:00 (Asia/Bishkek).
# Слоты берём с шагом в час и мимо обеда, чтобы приём любой разумной длительности
# попадал в рабочее окно и не пересекался с соседним.
_SAFE_HOURS = (9, 10, 11, 13, 14, 15, 16, 17)
_slot_seq = itertools.count()


def working_slot() -> str:
    """Уникальный слот в рабочем окне клиники — для ``scheduledAt``.

    Дата у приёма обязательна (дефолта «сейчас» нет: он превращал опечатку в
    имени поля в тихую запись «на текущий момент»), а часы клиники в
    большинстве тестов дефолтные. Хелпер выдаёт заведомо рабочее время, каждый
    раз новое, — чтобы тесты не зависели ни от времени запуска, ни друг от друга.
    """
    index = next(_slot_seq)
    day = date.today() + timedelta(days=1 + index // len(_SAFE_HOURS))
    if day.weekday() == 6:  # воскресенье — выходной по умолчанию
        day += timedelta(days=1)
    hour = _SAFE_HOURS[index % len(_SAFE_HOURS)]
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


def today_slot(hour: int = 10) -> str:
    """Слот СЕГОДНЯ — для отчётов: пресет периода кончается концом текущего дня.

    Приём на завтра в отчёт за период не попадёт, поэтому там, где проверяется
    именно отчёт, дата должна быть сегодняшней.
    """
    return f"{date.today().isoformat()}T{hour:02d}:00:00+06:00"

# Мутируемые таблицы — чистятся между тестами (в FK-безопасном порядке,
# дети → родители). Справочники и шаблоны ролей (clinic_id IS NULL) остаются.
_CLEAN_SQL = [
    "DELETE FROM role_permission WHERE role_id IN "
    "(SELECT id FROM role WHERE clinic_id IS NOT NULL)",
    "DELETE FROM audit_log",
    # Клиническое ядро v2: приём ссылается на membership/clinic/service, поэтому
    # чистится до них. Пациент глобальный — чистится вместе с остальными.
    "DELETE FROM appointment_item_tooth",
    "DELETE FROM appointment_item",
    "DELETE FROM appointment",
    "DELETE FROM patient_tooth",
    "DELETE FROM service",
    "DELETE FROM patient",
    "DELETE FROM tariff_package",
    "DELETE FROM tariff_version",
    "DELETE FROM subscription_event",
    "DELETE FROM subscription",
    "DELETE FROM membership",
    "DELETE FROM role WHERE clinic_id IS NOT NULL",
    "DELETE FROM lead",
    # Общие шаблоны WhatsApp (clinic_id IS NULL) каскадом от клиники не
    # чистятся; диалоги/сообщения/привязки уходят каскадом вместе с clinic.
    "DELETE FROM whatsapp_template",
    "DELETE FROM clinic",
    "DELETE FROM account",
    # Журнал воркера ни на кого не ссылается, но копится между тестами и ломает
    # проверки «в журнале ровно один запуск».
    "DELETE FROM job_run",
]


@dataclass(slots=True)
class Resources:
    factory: async_sessionmaker[AsyncSession]
    fake: Redis


def _ensure_docker_env() -> None:
    """Найти docker-сокет (Docker Desktop кладёт его в контекст пользователя).

    testcontainers через docker-py читает ``DOCKER_HOST``/дефолтный сокет. Если
    ни того, ни другого нет — берём endpoint из ``docker context inspect``.
    Ryuk (reaper) отключаем: контейнер чистится context-manager'ом.
    """
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    if os.environ.get("DOCKER_HOST") or os.path.exists("/var/run/docker.sock"):
        return
    try:
        out = subprocess.run(
            ["docker", "context", "inspect"], capture_output=True, text=True, check=True
        ).stdout
        os.environ["DOCKER_HOST"] = json.loads(out)[0]["Endpoints"]["docker"]["Host"]
    except Exception:  # pragma: no cover — CI использует стандартный сокет
        pass


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """Эфемерный Postgres + миграции; отдаёт asyncpg-URL (один раз на сессию)."""
    if PostgresContainer is None:
        pytest.skip("testcontainers/Docker недоступны")
    _ensure_docker_env()
    with PostgresContainer(
        "postgres:16-alpine", username="test", password="test", dbname="test"
    ) as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        url = f"postgresql+asyncpg://test:test@{host}:{port}/test"
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_REPO_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            check=True,
        )
        yield url


@pytest_asyncio.fixture
async def _resources(pg_url: str) -> AsyncIterator[Resources]:
    """Тестовый движок + fakeredis + оверрайды зависимостей; чистая БД на тест."""
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    fake = aioredis.FakeRedis(decode_responses=True)

    async def _get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[get_redis] = lambda: fake

    async with engine.begin() as conn:
        for stmt in _CLEAN_SQL:
            await conn.execute(text(stmt))

    yield Resources(factory=factory, fake=fake)

    app.dependency_overrides.clear()
    await fake.aclose()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(_resources: Resources) -> AsyncIterator[AsyncClient]:
    """httpx-клиент поверх ASGI (единый контур, тестовая БД)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _create_account(res: Resources, **kwargs: object) -> None:
    async with res.factory() as session:
        session.add(Account(**kwargs))
        await session.commit()


async def otp_code(phone: str, purpose: str = "login") -> str:
    """Прочитать выпущенный OTP-код из fakeredis (канал доставки заглушён).

    Redis берём из подмены зависимости в самом приложении, а не из фикстуры
    ``_resources``: хелпер входа зовётся из десятков тестовых файлов, и тащить
    фикстуру в каждую вспомогательную функцию — лишний параметр в каждой сигнатуре.
    """
    redis: Redis = app.dependency_overrides[get_redis]()
    code: str | None = await redis.get(f"auth:otp:code:{purpose}:{normalize_phone(phone)}")
    assert code is not None, f"код назначения {purpose} не выпущен"
    return code


_doctor_phone_seq = itertools.count(1)


async def own_membership_id(client: AsyncClient, headers: dict[str, str]) -> str:
    """membershipId практикующего врача клиники; заводит его, если такого нет.

    Врач у приёма обязателен во всех статусах, кроме черновика, а «врач» — это
    активное членство **со специализацией**. Владелец клиники ей не обладает
    (её получает только соло-врач при регистрации), поэтому подставить его
    членство нельзя — приём отвергается как ``invalid_doctor``.

    В соло-кабинете врач уже есть — сам владелец, и заводить некого: сотрудников
    соло не поддерживает вовсе.
    """
    members = await client.get(f"{API}/clinic/staff", headers=headers)
    assert members.status_code == 200, members.text
    existing = next(
        (m for m in members.json() if m["active"] and m["specializationId"]), None
    )
    if existing is not None:
        return str(existing["membershipId"])

    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    added = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={
            "fullName": "Врач Тестовый",
            "phone": f"07007{next(_doctor_phone_seq):05d}",
            "specializationId": spec,
        },
    )
    assert added.status_code == 201, added.text
    return str(added.json()["membershipId"])


async def otp_login(client: AsyncClient, phone: str) -> dict[str, str]:
    """Войти по OTP и вернуть заголовок авторизации.

    Основной вход платформы: клиники и соло-врачи. Команда Skrezo так не входит,
    ей нужен ``POST /auth/login`` (см. фикстуру ``admin_token``).
    """
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text

    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["status"] == "authenticated", body
    return {"Authorization": f"Bearer {body['auth']['accessToken']}"}


@pytest_asyncio.fixture
async def admin_token(_resources: Resources, client: AsyncClient) -> str:
    """Создать платформенного админа и войти — вернуть access-токен."""
    await _create_account(
        _resources,
        phone=normalize_phone("0700000001"),
        full_name="Test Admin",
        lang=Lang.RU,
        onboarded=True,
        password_hash=hash_password("admin123"),
        must_change_password=False,
        is_platform_admin=True,
    )
    resp = await client.post(
        f"{API}/auth/login", json={"phone": "0700000001", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["accessToken"])


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, admin_token: str) -> AsyncClient:
    """Клиент с заголовком авторизации платформенного админа."""
    client.headers["Authorization"] = f"Bearer {admin_token}"
    return client
