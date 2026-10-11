"""Отправка уведомлений — очередью и без права уронить операцию.

Два правила, из-за которых уведомления не зовут провайдера прямо в обработчике:

* **Ответ не ждёт провайдера.** Цепочка каналов в худшем случае — это два
  таймаута подряд; добавлять их к добавлению сотрудника или к публичной
  лид-форме нельзя.
* **Недоставка не отменяет сделанное.** Сотрудник добавлен, клиника создана,
  лид записан — уведомление про это вторично.

Отправка идёт **задачей в очередь** (arq), а не фоновой задачей запроса: у
очереди есть ретраи и она переживает рестарт процесса. Раньше сообщение,
попавшее между ответом и отправкой, терялось молча.

Если очередь недоступна (Redis лёг, а в тестах её просто нет — lifespan там не
запускается), сообщение уходит **на месте**: потерять уведомление хуже, чем
задержать ответ на время таймаута. Такой путь виден в логе как
``notification_sent_inline``.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from app.core.jobs import SEND_NOTIFICATION, SEND_TEAM_NOTIFICATION, JobQueue
from app.shared.notifications.channels import ChatButton, ChatChannel, MessageDeliveryError
from app.shared.notifications.delivery import TextDeliveryService

logger = structlog.get_logger(__name__)


class Notifier:
    """Ставит доставку в очередь: на телефон (цепочка каналов) или в чат команды."""

    def __init__(
        self,
        queue: JobQueue,
        delivery: TextDeliveryService,
        team_chat: ChatChannel | None = None,
    ) -> None:
        self._queue = queue
        self._delivery = delivery
        self._team_chat = team_chat

    async def notify_phone(self, phone: str, text: str, *, event: str) -> None:
        """Поставить в очередь сообщение на телефон; ``event`` — метка для логов."""
        if await self._queue.enqueue(SEND_NOTIFICATION, phone, text, event):
            return
        await self._deliver_phone_now(phone, text, event)

    async def notify_team(
        self,
        text: str,
        *,
        event: str,
        html: bool = False,
        buttons: Sequence[ChatButton] = (),
    ) -> None:
        """Поставить в очередь сообщение в чат команды Skrezo.

        ``html`` и ``buttons`` — для карточек: текст размечен HTML (всё
        динамическое в нём экранировано), под сообщением — кнопки-ссылки.

        Чат не настроен (нет бот-токена) — событие просто уходит в лог: это
        рабочее состояние на dev-стенде, а не повод падать. Проверяем это здесь,
        до очереди, чтобы не гонять воркер ради заведомо пустой работы.
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
        if await self._queue.enqueue(SEND_TEAM_NOTIFICATION, text, event, html, tuple(buttons)):
            return
        await self._deliver_team_now(text, event, html=html, buttons=buttons)

    async def _deliver_phone_now(self, phone: str, text: str, event: str) -> None:
        try:
            await self._delivery.send(phone, text)
        except MessageDeliveryError as exc:
            logger.error("notification_failed", notification=event, phone=phone, error=str(exc))
            return
        logger.info("notification_sent_inline", notification=event, phone=phone)

    async def _deliver_team_now(
        self, text: str, event: str, *, html: bool, buttons: Sequence[ChatButton]
    ) -> None:
        if self._team_chat is None:  # pragma: no cover — проверено в notify_team
            return
        try:
            await self._team_chat.send(text, html=html, buttons=buttons)
        except MessageDeliveryError as exc:
            logger.error("team_notification_failed", notification=event, error=str(exc))
            return
        logger.info("team_notification_sent_inline", notification=event)
