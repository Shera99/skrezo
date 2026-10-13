"""Счётчик использования — единственная точка, откуда растут счётчики.

Зовут его три места: конвейер ассистента (вызовы модели и ответы), резюме
диалогов (вызовы модели) и разбор статусов WhatsApp (категория и платность).
Все инкременты — в транзакции вызывающего: упавший ход не оставит «учтённых»
токенов, которых не было.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.infrastructure.repositories.usage_repository import UsageRepository
from app.shared.db.models.ai_conversation import AiConversation

#: Категория платности из статуса Меты → счётчик.
_BILLABLE_COUNTERS = {
    "utility": "wa_utility",
    "marketing": "wa_marketing",
    "authentication": "wa_authentication",
}


class UsageMeter:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = UsageRepository(session)

    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()

    async def model_call(self, clinic_id: uuid.UUID, usage: Mapping[str, int] | None) -> None:
        """Один вызов модели: токены запроса и ответа, если провайдер их отдал."""
        usage = usage or {}
        await self._repository.add(
            clinic_id,
            self._today(),
            model_calls=1,
            model_input_tokens=int(usage.get("input") or 0),
            model_output_tokens=int(usage.get("output") or 0),
            model_cached_tokens=int(usage.get("cached") or 0),
        )

    async def voice_call(self, clinic_id: uuid.UUID, usage: Mapping[str, int] | None) -> None:
        """Одно обращение голосового помощника приложения: токены запроса и ответа."""
        usage = usage or {}
        await self._repository.add(
            clinic_id,
            self._today(),
            voice_calls=1,
            voice_input_tokens=int(usage.get("input") or 0),
            voice_output_tokens=int(usage.get("output") or 0),
        )

    async def assistant_reply(self, conversation: AiConversation) -> None:
        """Ответ ассистента (отправленный, черновик или теневой) и диалог — один раз."""
        dialogs = 0
        if conversation.assistant_counted_at is None:
            # Пакет тарифа считает ДИАЛОГИ, а не сообщения: длинная переписка —
            # один диалог, как бы долго ни длилась.
            conversation.assistant_counted_at = datetime.now(UTC)
            dialogs = 1
        await self._repository.add(
            conversation.clinic_id, self._today(), assistant_replies=1, dialogs=dialogs
        )

    async def whatsapp_status(
        self,
        clinic_id: uuid.UUID,
        *,
        first_seen: bool,
        billable: bool | None,
        category: str | None,
    ) -> None:
        """Статус доставки Меты: исходящее посчитано раз, платность — раз."""
        increments: dict[str, int] = {}
        if first_seen:
            increments["wa_sent"] = 1
        if billable is True:
            increments[_BILLABLE_COUNTERS.get(category or "", "wa_billable_other")] = 1
        elif billable is False:
            increments["wa_service_free"] = 1
        if increments:
            await self._repository.add(clinic_id, self._today(), **increments)
