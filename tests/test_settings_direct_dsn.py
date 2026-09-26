"""Прямой DSN мимо пулера: слушатели pg_notify и миграции не ходят через PgBouncer.

PgBouncer в transaction-режиме не переживает ``LISTEN``: сигнал приходит на
серверное соединение, которое к тому моменту отдано другому клиенту. Поэтому
слушатели и Alembic берут ``DATABASE_DIRECT_URL`` (порт самого Postgres), а
без пулера — тот же ``DATABASE_URL``.
"""

from __future__ import annotations

from app.core.config import Settings

POOLED = "postgresql+asyncpg://skrezo:secret@127.0.0.1:6432/skrezo"
DIRECT = "postgresql+asyncpg://skrezo:secret@127.0.0.1:5432/skrezo"


def test_direct_dsn_falls_back_to_database_url() -> None:
    """Без пулера прямой адрес не задают — LISTEN идёт по DATABASE_URL, но без драйвера."""
    s = Settings(database_url=POOLED, database_direct_url="")
    assert s.direct_database_url == POOLED
    assert s.listen_dsn == "postgresql://skrezo:secret@127.0.0.1:6432/skrezo"


def test_direct_dsn_wins_when_set() -> None:
    """С PgBouncer LISTEN и Alembic обязаны идти на прямой порт Postgres."""
    s = Settings(database_url=POOLED, database_direct_url=DIRECT)
    assert s.direct_database_url == DIRECT
    assert s.listen_dsn == "postgresql://skrezo:secret@127.0.0.1:5432/skrezo"
