"""Схемы модуля AI-ассистента."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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
        min_length=5,
        description="Номер «пациента» (E.164 или локальный)",
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
    outcome: str = Field(description="sent | shadow | draft | escalated | skipped | deferred")
    bot_reply: str | None = Field(default=None, description="Что ответил (или предложил) бот")
    escalation_reason: str | None = None
    messages: list[SimulatedMessageOut] = Field(description="Хвост диалога, новые снизу")


class DraftSendRequest(CamelModel):
    """Отправить черновик; ``body`` — правка текста перед отправкой."""

    body: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description="Текст с правкой администратора; пусто — отправить как предложил бот",
        examples=["Здравствуйте! Профессиональная гигиена стоит 1 500 сом."],
    )


class AssistantToggleRequest(CamelModel):
    """Кто ведёт диалог: ``false`` — «дальше отвечаю сам», ``true`` — вернуть боту."""

    active: bool = Field(description="`true` — бот снова отвечает, `false` — диалог у человека")


class AssistantToggleResponse(CamelModel):
    conversation_id: uuid.UUID
    status: str = Field(description="Статус диалога после переключения: active / escalated")
    bot_active: bool = Field(description="Отвечает ли бот в этом диалоге")
    resolved_escalations: int = Field(
        default=0, description="Сколько открытых эскалаций закрыто возвратом боту"
    )


class KnowledgeGapOut(CamelModel):
    """Вопрос без ответа: один смысл, похожие формулировки склеены."""

    id: uuid.UUID
    question: str = Field(description="Дословно, как спросил пациент в первый раз")
    lang: str = Field(description="ru | ky — на этом языке родится документ базы знаний")
    status: str = Field(
        description="unknown — не знает, answered — знает, dismissed — не нужно отвечать"
    )
    source: str = Field(
        description=(
            "low_confidence — «не знаю» модели, no_facts — конкретика без фактов, "
            "model_unknown — escalate(unknown)"
        )
    )
    hint: str | None = Field(default=None, description="Что модель сказала о пробеле")
    ask_count: int = Field(description="Сколько раз спрашивали (с учётом склейки)", examples=[4])
    first_asked_at: datetime
    last_asked_at: datetime
    conversation_ids: list[uuid.UUID] = Field(description="Последние диалоги, где спрашивали")
    kb_document_id: uuid.UUID | None = Field(default=None, description="Чему бот научился")
    resolved_at: datetime | None = None

    @classmethod
    def from_orm_gap(cls, g: Any) -> KnowledgeGapOut:
        return cls(
            id=g.id,
            question=g.question,
            lang=g.lang,
            status=g.status,
            source=g.source,
            hint=g.hint,
            ask_count=g.ask_count,
            first_asked_at=g.first_asked_at,
            last_asked_at=g.last_asked_at,
            conversation_ids=[uuid.UUID(str(c)) for c in (g.conversation_ids or [])],
            kb_document_id=g.kb_document_id,
            resolved_at=g.resolved_at,
        )


class KnowledgeGapCounts(CamelModel):
    """Числа вкладок экрана «Обучение»."""

    unknown: int = Field(description="Не знает", examples=[3])
    answered: int = Field(description="Знает", examples=[3])
    dismissed: int = Field(description="Не нужно отвечать", examples=[1])


class KnowledgeGapsResponse(CamelModel):
    list: list[KnowledgeGapOut]
    counts: KnowledgeGapCounts
    current_page: int
    page_size: int = 25
    total_count: int


class AnswerKnowledgeGapRequest(CamelModel):
    """Ответ администратора своими словами."""

    answer: str = Field(
        min_length=3,
        max_length=4000,
        description="Текст ответа — станет документом базы знаний; без цен",
        examples=["Да, у нас бесплатная парковка во дворе, въезд с улицы Токтогула."],
    )
    title: str | None = Field(
        default=None, max_length=200, description="Заголовок документа; пусто — сам вопрос"
    )
    category: str = Field(
        default="faq",
        description="Категория базы знаний: logistics | aftercare | faq | policy | payment",
        examples=["logistics"],
    )
