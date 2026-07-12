"""Асинхронный доступ к PostgreSQL (SQLAlchemy 2.0 + asyncpg).

Здесь создаётся движок и фабрика сессий, а также FastAPI-зависимость
:func:`get_session`, отдающая сессию на время обработки запроса.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# Один движок на процесс; NullPool не нужен — используем встроенный пул.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug and not settings.is_production,
    pool_pre_ping=True,
)

# expire_on_commit=False — объекты остаются пригодными после commit
# (удобно возвращать их из сервисов, не подгружая заново).
session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: сессия БД с автозакрытием.

    Коммит/роллбак — ответственность прикладного слоя (Unit of Work),
    поэтому здесь только гарантированное закрытие соединения.
    """
    async with session_factory() as session:
        yield session
