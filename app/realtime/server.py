"""Socket.IO-сервер: авторизация при подключении и комнаты по клиникам (§9).

Правила, каждое закрывает конкретную дыру:

* токен проверяется ПРИ ПОДКЛЮЧЕНИИ — тот же JWT, что в REST (по нему клиент
  и так ходит за данными: сокет несёт только сигналы «перечитай»);
* комнаты определяет СЕРВЕР по живым членствам аккаунта — имя комнаты от
  клиента не принимается никогда, обработчиков подписки просто не существует
  (иначе врач подписался бы на чужую клинику одной строкой);
* сотрудник двух клиник входит в обе комнаты;
* пинг штатный Socket.IO, интервал 25 с — меньше ~100 с, после которых прокси
  Cloudflare рвёт простаивающее соединение.

Пропущенные события не переигрываются: при восстановлении связи клиент сам
перезапрашивает активные ключи и инбокс — по сокету шли только сигналы.
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt as pyjwt
import socketio
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import decode_token
from app.shared.db.models.membership import Membership

logger = structlog.get_logger(__name__)

#: Socket.IO-сервер процесса. Комнаты живут в его памяти — межпроцессную
#: доставку обеспечивает шина pg_notify (см. ``app/realtime/listener.py``).
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.cors_origins or "*",
    # Пинг чаще, чем Cloudflare режет простой (~100 с).
    ping_interval=25,
    ping_timeout=20,
)


def room_of(clinic_id: uuid.UUID | str) -> str:
    """Имя комнаты клиники — единственный формат, никаких клиентских имён."""
    return f"clinic:{clinic_id}"


async def authorize_rooms(session: AsyncSession, token: str | None) -> list[str]:
    """Проверить access-токен и вернуть комнаты по ЖИВЫМ членствам аккаунта.

    Уволенный (``membership.active = false``) комнат не получает; сотрудник
    двух клиник получает обе. Аккаунт без членств подключается без комнат —
    сигналов ему не будет, но и ошибки нет (свежесозданный соло-врач).
    """
    if not token:
        raise UnauthorizedError("Требуется авторизация")
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError as exc:
        raise UnauthorizedError("Невалидный токен", code="invalid_token") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Ожидался access-токен", code="invalid_token")
    account_id = uuid.UUID(str(payload["sub"]))
    clinic_ids = (
        (
            await session.execute(
                select(Membership.clinic_id).where(
                    Membership.account_id == account_id,
                    Membership.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return [room_of(clinic_id) for clinic_id in clinic_ids]


def _extract_token(environ: dict[str, Any], auth: dict[str, Any] | None) -> str | None:
    """Токен из handshake: поле ``auth.token`` (штатный путь socket.io-client)
    либо заголовок ``Authorization: Bearer …`` — как в REST."""
    if auth and auth.get("token"):
        return str(auth["token"])
    header = str(environ.get("HTTP_AUTHORIZATION") or "")
    if header.lower().startswith("bearer "):
        return header[7:]
    return None


@sio.event  # type: ignore[untyped-decorator]
async def connect(sid: str, environ: dict[str, Any], auth: dict[str, Any] | None) -> None:
    """Впустить клиента и подписать на комнаты его клиник (сервер решает сам)."""
    from app.core.db import session_factory

    token = _extract_token(environ, auth)
    try:
        async with session_factory() as session:
            rooms = await authorize_rooms(session, token)
    except UnauthorizedError as exc:
        raise socketio.exceptions.ConnectionRefusedError(exc.code) from exc
    for room in rooms:
        await sio.enter_room(sid, room)
    logger.info("realtime_connected", sid=sid, rooms=len(rooms))


@sio.event  # type: ignore[untyped-decorator]
async def disconnect(sid: str) -> None:
    logger.debug("realtime_disconnected", sid=sid)


def build_asgi(fastapi_app: Any) -> socketio.ASGIApp:
    """Обернуть FastAPI: ``/socket.io/`` обслуживает шлюз, остальное — API.

    Lifespan-события socketio пробрасывает вложенному приложению — слушатель
    шины стартует из lifespan FastAPI как обычно.
    """
    return socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
