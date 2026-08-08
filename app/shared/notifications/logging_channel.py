"""Канал «в лог» — для разработки и тестов.

Работает, когда провайдеры не настроены (``NOTIFY_CHANNELS`` пуст): текст виден
в логе. Так локальная разработка не требует боевых токенов, а тесты не ходят в
сеть.

⚠️ В проде так выпускать нельзя: в тексте уведомления бывает временный пароль,
а значит в логе он окажется у всех, у кого есть доступ к логам. Приложение
предупреждает на старте, если каналы не настроены (см. ``build_text_channels``).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class LoggingTextChannel:
    """Пишет сообщение в лог вместо отправки."""

    name = "log"

    async def send_text(self, phone: str, text: str) -> None:
        logger.info("notification_logged", phone=phone, text=text)
