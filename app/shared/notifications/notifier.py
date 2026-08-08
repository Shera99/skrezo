"""Отправка уведомлений — фоном и без права уронить операцию.

Два правила, из-за которых уведомления не зовут провайдера прямо в обработчике:

* **Ответ не ждёт провайдера.** Цепочка каналов в худшем случае — это два
  таймаута подряд; добавлять их к добавлению сотрудника или к публичной
  лид-форме нельзя. Отправка уходит в ``BackgroundTasks``: клиент получает
  ответ, доставка идёт после.
* **Недоставка не отменяет сделанное.** Сотрудник добавлен, клиника создана,
  лид записан — уведомление про это вторично. Ошибка канала уходит в лог с
  событием, а наружу не выносится.

Обратная сторона фонового режима: перезапуск процесса между ответом и отправкой
теряет сообщение. Для временного пароля это терпимо — он остаётся в ответе API,
и его видит тот, кто заводил сотрудника. Когда появятся джобы биллинга, отправку
логично будет перевести на arq с ретраями.
"""

from __future__ import annotations

import structlog
from fastapi import BackgroundTasks

from app.shared.notifications.channels import ChatChannel, MessageDeliveryError
from app.shared.notifications.delivery import TextDeliveryService

logger = structlog.get_logger(__name__)


class Notifier:
    """Ставит доставку в фон: на телефон (цепочка каналов) или в чат команды."""

    def __init__(
        self,
        background: BackgroundTasks,
        delivery: TextDeliveryService,
        team_chat: ChatChannel | None = None,
    ) -> None:
        self._background = background
        self._delivery = delivery
        self._team_chat = team_chat

    def notify_phone(self, phone: str, text: str, *, event: str) -> None:
        """Поставить в очередь сообщение на телефон; ``event`` — метка для логов."""
        self._background.add_task(self._deliver_phone, phone, text, event)

    def notify_team(self, text: str, *, event: str) -> None:
        """Поставить в очередь сообщение в чат команды Skrezo.

        Чат не настроен (нет бот-токена) — событие просто уходит в лог: это
        рабочее состояние на dev-стенде, а не повод падать.
        """
        if self._team_chat is None:
            # ⚠️ Метку кладём под именем notification, а не event: у structlog
            # первый позиционный аргумент называется event, и ключ с тем же
            # именем роняет сам вызов логирования.
            logger.info(
                "team_notification_skipped",
                notification=event,
                reason="chat_not_configured",
            )
            return
        self._background.add_task(self._deliver_team, text, event)

    async def _deliver_phone(self, phone: str, text: str, event: str) -> None:
        try:
            await self._delivery.send(phone, text)
        except MessageDeliveryError as exc:
            logger.error("notification_failed", notification=event, phone=phone, error=str(exc))

    async def _deliver_team(self, text: str, event: str) -> None:
        if self._team_chat is None:  # pragma: no cover — проверено в notify_team
            return
        try:
            await self._team_chat.send(text)
        except MessageDeliveryError as exc:
            logger.error("team_notification_failed", notification=event, error=str(exc))
