"""Жизненный цикл диалога WhatsApp: один диалог на номер, закрытый — возобновляется.

Диалог пары (клиника, номер) — единственный и постоянный. «Закрыт» — не конец
переписки, а пауза дольше суток (§8.4): по закрытому эпизоду строится резюме,
а следующее сообщение продолжает тот же диалог. Так у пациента в кабинете
одна переписка, а не новая строка после каждого перерыва.
"""

from __future__ import annotations

from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation


def reopen_if_closed(conversation: AiConversation) -> bool:
    """Вернуть закрытый диалог в ``active``; ``True`` — статус изменился.

    Эскалированный не трогаем: человек ведёт разговор, бот молчит до возврата.
    Отметка «диалог посчитан в пакет» сбрасывается: пакет тарифа считает
    обращения, и обращение после суток тишины — новое, как раньше был новый
    диалог. Иначе постоянный диалог считался бы один раз за всю жизнь пациента.
    """
    if conversation.status is not AiConversationStatus.CLOSED:
        return False
    conversation.status = AiConversationStatus.ACTIVE
    conversation.assistant_counted_at = None
    return True
