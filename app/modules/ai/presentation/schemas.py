"""Схемы модуля AI-ассистента."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.shared.api.schemas import CamelModel
from app.shared.db.enums import AiMode, GlossaryStatus
from app.shared.db.models.glossary_term import GlossaryTerm


class GlossaryTermOut(CamelModel):
    """Термин глоссария; ``shared`` — общий словарь платформы (только чтение)."""

    id: uuid.UUID
    phrase: str
    meaning: str
    lang: str | None = None
    source: str = Field(description="manual | assistant | admin_feedback")
    status: GlossaryStatus
    shared: bool
    created_at: datetime

    @classmethod
    def from_orm_term(cls, t: GlossaryTerm) -> GlossaryTermOut:
        return cls(
            id=t.id,
            phrase=t.phrase,
            meaning=t.meaning,
            lang=t.lang,
            source=t.source,
            status=t.status,
            shared=t.clinic_id is None,
            created_at=t.created_at,
        )


class GlossaryListResponse(CamelModel):
    list: list[GlossaryTermOut]


class CreateGlossaryTermRequest(CamelModel):
    """Термин от администратора — сразу approved."""

    phrase: str = Field(min_length=1, max_length=200, examples=["тиш ооруп жатат"])
    meaning: str = Field(min_length=1, max_length=500, examples=["болит зуб"])
    lang: str | None = Field(default=None, description="ru | ky | mixed")


class SetAiModeRequest(CamelModel):
    """Режим ассистента клиники (§13.1)."""

    mode: AiMode = Field(
        description=(
            "`off` — выключен; `shadow` — готовит ответы, пациенту не пишет; "
            "`assisted` — пока как shadow; `auto` — отвечает сам после валидатора"
        ),
        examples=["shadow"],
    )


class AiModeResponse(CamelModel):
    clinic_id: uuid.UUID
    mode: AiMode


class SimulateInboundRequest(CamelModel):
    """Имитация входящего WhatsApp-сообщения — без Meta и подписи."""

    clinic_id: uuid.UUID
    from_phone: str = Field(
        min_length=5, description="Номер «пациента» (E.164 или локальный)",
        examples=["+996700123456"],
    )
    text: str = Field(min_length=1, max_length=4096, examples=["Канча турат чистка?"])
    button_payload: str | None = Field(
        default=None,
        description=(
            "Имитация нажатия кнопки подтверждения: `confirmationId` из карточки "
            "propose_appointment (`hold:<uuid>`); `text` при этом игнорируется"
        ),
        examples=["hold:6f1e0e6a-…"],
    )


class SimulatedMessageOut(CamelModel):
    role: str
    body: str
    sent: bool = Field(description="Ушло ли пациенту (в shadow ответы бота не уходят)")
    model: str | None = None


class SimulateInboundResponse(CamelModel):
    """Результат прогона: как отработал настоящий конвейер."""

    conversation_id: uuid.UUID
    outcome: str = Field(description="sent | shadow | escalated | skipped")
    bot_reply: str | None = Field(default=None, description="Что ответил (или предложил) бот")
    escalation_reason: str | None = None
    messages: list[SimulatedMessageOut] = Field(description="Хвост диалога, новые снизу")
