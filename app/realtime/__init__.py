"""Socket.IO-шлюз (Ф4): живое обновление открытых экранов."""

from __future__ import annotations

from app.realtime.server import authorize_rooms, build_asgi, sio

__all__ = ["authorize_rooms", "build_asgi", "sio"]
