"""Карточки клиентов в канал команды Skrezo: новая регистрация и конец триала.

Сервис живёт в модуле «Клиенты», потому что карточка — это клиент глазами
команды, а поводов её прислать несколько и они в разных модулях: регистрация
соло-врача (``identity``) и конец пробного периода (``subscriptions``).

Вызывать — только после коммита: карточка о том, что не сохранилось, хуже, чем
никакой. Недоставка и сбой чтения карточки операцию не отменяют — клиент уже
зарегистрирован, триал уже закрыт.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.modules.clients.domain.models import ClientCard
from app.modules.clients.domain.team_messages import (
    card_buttons,
    client_registered_text,
    trial_expired_text,
)
from app.modules.clients.infrastructure.repositories.client_card_repository import (
    ClientCardRepository,
)
from app.shared.notifications.notifier import Notifier

logger = structlog.get_logger(__name__)


class ClientTeamCardService:
    def __init__(self, cards: ClientCardRepository, notifier: Notifier) -> None:
        self._cards = cards
        self._notifier = notifier

    async def notify_registered(self, clinic_id: uuid.UUID) -> None:
        """Карточка «новая регистрация» — клиент сам завёл себе аккаунт."""
        for card in await self._load([clinic_id], event="client_registered"):
            await self._send(client_registered_text(card), card, event="client_registered")

    async def notify_trial_expired(self, clinic_ids: Sequence[uuid.UUID]) -> None:
        """По карточке на каждую клинику, у которой только что кончился триал.

        Отдельными сообщениями, а не одним списком: у каждой клиники свои
        контакты и кнопки «написать», и обсуждать в канале их тоже удобнее по
        одной (ответ на карточку — это разговор именно о ней).
        """
        for card in await self._load(clinic_ids, event="trial_expired"):
            await self._send(trial_expired_text(card), card, event="trial_expired")

    async def _load(self, clinic_ids: Sequence[uuid.UUID], *, event: str) -> list[ClientCard]:
        # Сбой чтения после коммита не должен превращать успешную регистрацию в
        # 500, а прогон джобы — в падение: теряем карточку, но не операцию.
        try:
            return await self._cards.cards(clinic_ids)
        except SQLAlchemyError as exc:
            logger.error("team_card_load_failed", notification=event, error=str(exc))
            return []

    async def _send(self, text: str, card: ClientCard, *, event: str) -> None:
        await self._notifier.notify_team(text, event=event, html=True, buttons=card_buttons(card))
