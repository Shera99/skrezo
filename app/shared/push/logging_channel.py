"""Канал «в лог» — работает, пока FCM/APNs не настроены (кредов ещё нет).

Пуш виден в логе вместо отправки: локальная разработка и тесты не требуют боевых
ключей и не ходят в сеть. Фабрика (:func:`app.shared.push.factory.build_push_transport`)
предупреждает на старте, если это включено в проде.
"""

from __future__ import annotations

import structlog

from app.shared.push.message import PushMessage

logger = structlog.get_logger(__name__)


class LoggingPushChannel:
    """Пишет пуш в лог вместо доставки на устройство."""

    name = "log"

    async def send(self, token: str, message: PushMessage) -> None:
        # Токен усечён: целиком в логе он не нужен, а это чувствительная строка.
        logger.info(
            "push_logged",
            token=token[:12],
            type=message.data.get("type"),
            silent=message.is_silent,
            title=message.title,
            urgent=message.urgent,
        )
