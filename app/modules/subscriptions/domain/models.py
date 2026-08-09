"""Доменные данные подписки для фоновых задач."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SubscriptionContact:
    """Клиника вместе с тем, кому писать про её подписку.

    Собирается одним запросом рядом с изменением статуса: джоба иначе шла бы за
    телефоном владельца по одной клинике на сообщение.

    ``deadline`` — тот срок, о котором идёт речь в конкретной задаче: конец
    триала или конец оплаченного периода. Двух отдельных полей здесь нет
    намеренно — задача всегда знает, какой срок она обрабатывает, а два поля
    рядом только приглашают взять не то.
    """

    subscription_id: uuid.UUID
    clinic_id: uuid.UUID
    clinic_name: str
    deadline: datetime
    #: Телефон владельца; ``None`` — у клиники нет активного владельца (писать некому).
    owner_phone: str | None
