"""Фикстуры интеграционных API-тестов.

Поднимает эфемерный Postgres (testcontainers), прогоняет на нём реальные миграции
Alembic (схема + сиды справочников/ролей), подменяет зависимости приложения
``get_session`` (тестовый движок) и ``get_redis`` (fakeredis) и отдаёт httpx-клиент
поверх ASGI. Между тестами мутируемые таблицы чистятся, справочники/шаблоны — нет.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
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
    "DELETE FROM clinic",
    "DELETE FROM account",
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
