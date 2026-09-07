"""Общие httpx-клиенты процесса — один пул соединений на назначение (Ф8).

Клиент на каждый вызов — это TLS-рукопожатие на каждое сообщение WhatsApp и
каждый ход модели: при десятках тысяч запросов в сутки это заметная
задержка и лишние соединения к Meta/Google. Здесь клиенты живут столько,
сколько процесс: API закрывает их в lifespan, воркер — в shutdown.
"""

from __future__ import annotations

import contextlib

import httpx

_clients: dict[str, httpx.AsyncClient] = {}


def shared_async_client(name: str, *, timeout: float) -> httpx.AsyncClient:
    """Клиент по имени назначения; закрытый (после shutdown) создаётся заново."""
    client = _clients.get(name)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        _clients[name] = client
    return client


async def close_shared_clients() -> None:
    """Закрыть все пулы — на остановке процесса."""
    for name in list(_clients):
        client = _clients.pop(name)
        with contextlib.suppress(Exception):
            await client.aclose()
