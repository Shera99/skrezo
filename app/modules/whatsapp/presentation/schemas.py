"""Схемы модуля WhatsApp (переписка с пациентами)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import Field

from app.modules.whatsapp.domain.models import ConversationRow
from app.modules.whatsapp.domain.window import is_window_open
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import AiConversationStatus, AiMessageRole, Lang, WhatsAppMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.whatsapp_template import WhatsAppTemplate


class ConversationListItem(CamelModel):
    """Строка списка диалогов — шапка, превью и состояние для вкладок кабинета."""

    id: uuid.UUID
    wa_phone: str = Field(description="Номер пациента (E.164)", examples=["+996700123456"])
    patient_id: uuid.UUID | None = Field(
        default=None, description="Глобальный пациент, если номер опознан"
    )
    patient_name: str | None = None
    lang: Lang
    status: AiConversationStatus
    last_msg_at: datetime
    last_patient_msg_at: datetime | None = None
    window_open: bool = Field(
        description=(
            "Открыто ли 24-часовое окно Meta: `true` — можно свободный текст, "
            "`false` — только одобренные шаблоны"
        )
    )
    last_message_preview: str | None = Field(
        default=None, description="Начало последнего сообщения диалога"
    )
    last_message_role: AiMessageRole | None = Field(
        default=None, description="Кто написал последним: patient / bot / admin / system"
    )
    last_message_kind: str = Field(
        default="text",
        description="Вид последнего сообщения: `text` / `audio` / `image` / `document` / `other`",
        examples=["audio"],
    )
    last_message_duration_sec: int | None = Field(
        default=None, description="Длительность голосового, если последнее — аудио", examples=[14]
    )
    unread_count: int = Field(
        default=0,
        description="Непрочитанных сообщений пациента; отметка прочтения общая для клиники",
    )
    awaiting_reply: bool = Field(
        default=False, description="Последнее слово за пациентом — клиника ещё не ответила"
    )
    needs_human: bool = Field(
        default=False, description="Передан человеку либо ждёт решения по черновику"
    )
    pending_draft_id: int | None = Field(
        default=None, description="Черновик ассистента, ждущий одобрения (режим «Проверяю»)"
    )

    @classmethod
    def from_row(cls, r: ConversationRow, *, now: datetime) -> ConversationListItem:
        preview = (r.last_body or "").strip() or None
        if preview is not None and len(preview) > 120:
            preview = preview[:119] + "…"
        return cls(
            id=r.id,
            wa_phone=r.wa_phone,
            patient_id=r.patient_id,
            patient_name=r.patient_name,
            lang=r.lang,
            status=r.status,
            last_msg_at=r.last_msg_at,
            last_patient_msg_at=r.last_patient_msg_at,
            window_open=is_window_open(r.last_patient_msg_at, now),
            last_message_preview=preview,
            last_message_role=r.last_role,
            last_message_kind=_message_kind(r.last_media_type),
            last_message_duration_sec=r.last_media_duration_sec,
            unread_count=r.unread_count,
            awaiting_reply=r.awaiting_reply,
            needs_human=r.needs_human,
            pending_draft_id=r.pending_draft_id,
        )


def _message_kind(media_type: str | None) -> str:
    """Тип превью по словарю Meta: голосовое, снимок, документ — или текст."""
    if not media_type:
        return "text"
    if media_type == "audio":
        return "audio"
    if media_type in ("image", "sticker"):
        return "image"
    if media_type == "document":
        return "document"
    return "other"


class ConversationsResponse(CamelModel):
    list: list[ConversationListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class ConversationCountersResponse(CamelModel):
    """Числа вкладок списка и бейдж раздела «Ассистент»."""

    total: int = Field(description="Открытых диалогов (закрытые не считаются)", examples=[7])
    awaiting: int = Field(description="Ждут ответа: последнее слово за пациентом", examples=[3])
    needs_human: int = Field(
        description="Нужен человек: эскалация либо черновик на решении", examples=[2]
    )
    unread: int = Field(description="Диалогов с непрочитанным — число на бейдже", examples=[8])


class MarkReadRequest(CamelModel):
    """Докуда прочитано; пусто — до последнего сообщения."""

    up_to_message_id: int | None = Field(
        default=None, description="id последнего показанного сообщения", examples=[1042]
    )


class MarkReadResponse(CamelModel):
    read_message_id: int | None = Field(description="Отметка после сдвига (только вперёд)")
    unread_count: int = Field(description="Сколько сообщений пациента осталось непрочитанными")


class MessageOut(CamelModel):
    """Сообщение диалога."""

    id: int
    role: AiMessageRole
    body: str
    wa_message_id: str | None = None
    media_type: str | None = Field(
        default=None, description="Тип вложения по словарю Meta (audio/image/…)"
    )
    has_media: bool = Field(description="Скачано ли вложение (доступно ли по ручке медиа)")
    media_duration_sec: int | None = Field(
        default=None, description="Длительность голосового в секундах", examples=[14]
    )
    confidence: str | None = Field(
        default=None,
        description=(
            "Уверенность модели у ответов бота: `high` — «уверенный ответ», "
            "`medium` / `low` — «проверьте текст»"
        ),
        examples=["high"],
    )
    review: str | None = Field(
        default=None,
        description=(
            "Черновик режима «Проверяю»: `pending` / `approved` / `edited` / "
            "`discarded` / `superseded`; null — не черновик"
        ),
    )
    reviewed_by: uuid.UUID | None = Field(
        default=None, description="Кто принял решение по черновику (аккаунт сотрудника)"
    )
    reviewed_at: datetime | None = None
    original_body: str | None = Field(
        default=None, description="Что предлагал бот до правки человеком (`review = edited`)"
    )
    sent_at: datetime | None = Field(
        default=None, description="null — не отправлено (теневой режим, черновик или блок)"
    )
    created_at: datetime

    @classmethod
    def from_orm_message(cls, m: AiMessage) -> MessageOut:
        return cls(
            id=m.id,
            role=m.role,
            body=m.body,
            wa_message_id=m.wa_message_id,
            media_type=m.media_type,
            has_media=m.media_path is not None,
            media_duration_sec=m.media_duration_sec,
            confidence=m.confidence,
            review=m.review,
            reviewed_by=m.reviewed_by,
            reviewed_at=m.reviewed_at,
            original_body=m.original_body,
            sent_at=m.sent_at,
            created_at=m.created_at,
        )


class MessagesResponse(CamelModel):
    """История диалога: шапка + страница сообщений (новые сверху)."""

    conversation_id: uuid.UUID
    wa_phone: str
    patient_id: uuid.UUID | None = None
    patient_name: str | None = None
    lang: Lang
    status: AiConversationStatus
    window_open: bool
    items: list[MessageOut] = Field(
        description="Сообщения новыми сверху; продолжение — `beforeId` = id последнего"
    )

    @classmethod
    def build(
        cls,
        conversation: AiConversation,
        patient_name: str | None,
        items: list[AiMessage],
    ) -> MessagesResponse:
        return cls(
            conversation_id=conversation.id,
            wa_phone=conversation.wa_phone,
            patient_id=conversation.patient_id,
            patient_name=patient_name,
            lang=conversation.lang,
            status=conversation.status,
            window_open=is_window_open(conversation.last_patient_msg_at, datetime.now(UTC)),
            items=[MessageOut.from_orm_message(m) for m in items],
        )


class SendMessageRequest(CamelModel):
    """Свободный текст пациенту (только в открытое 24-часовое окно)."""

    body: str = Field(
        min_length=1,
        max_length=4096,
        description="Текст сообщения",
        examples=["Здравствуйте! Завтра в 11:00 вас ждёт Асель Кубатовна."],
    )


class ConnectWhatsAppRequest(CamelModel):
    """Привязка номера Cloud API к клинике (админка Skrezo)."""

    phone_number_id: str = Field(
        min_length=1,
        description="Идентификатор номера в Meta — по нему вебхук находит клинику",
        examples=["712233445566778"],
    )
    display_phone: str = Field(
        min_length=1, description="Сам номер (E.164)", examples=["+996700123456"]
    )
    waba_id: str | None = Field(
        default=None, description="WhatsApp Business Account — для синхронизации шаблонов"
    )
    mode: WhatsAppMode = Field(
        default=WhatsAppMode.API_ONLY,
        description="`api_only` — отдельный номер; `coexistence` — рабочий номер клиники",
    )
    active: bool = Field(default=True, description="`false` выключает приём вебхука")


class ClinicWhatsAppResponse(CamelModel):
    """Привязка номера клиники."""

    clinic_id: uuid.UUID
    phone_number_id: str
    display_phone: str
    waba_id: str | None = None
    mode: WhatsAppMode
    active: bool

    @classmethod
    def from_binding(cls, b: ClinicWhatsApp) -> ClinicWhatsAppResponse:
        return cls(
            clinic_id=b.clinic_id,
            phone_number_id=b.phone_number_id,
            display_phone=b.display_phone,
            waba_id=b.waba_id,
            mode=b.mode,
            active=b.active,
        )


class TemplateOut(CamelModel):
    """Одобренный (или ожидающий) шаблон WhatsApp."""

    id: uuid.UUID
    meta_name: str = Field(description="Имя шаблона в кабинете Meta")
    lang: str = Field(description="Код языка Meta", examples=["ru"])
    category: str = Field(examples=["utility"])
    body_preview: str = Field(description="Текст тела с плейсхолдерами {{1}}, {{2}}…")
    variables: list[str] = Field(
        description="Плейсхолдеры тела по порядку — столько переменных ждёт отправка"
    )
    status: str = Field(
        description="approved | pending | rejected | paused | disabled; отправка — только approved"
    )
    shared: bool = Field(description="Общий шаблон платформы (доступен всем клиникам)")

    @classmethod
    def from_orm_template(cls, t: WhatsAppTemplate) -> TemplateOut:
        return cls(
            id=t.id,
            meta_name=t.meta_name,
            lang=t.lang,
            category=t.category,
            body_preview=t.body_preview,
            variables=[str(v) for v in t.variables],
            status=t.status,
            shared=t.clinic_id is None,
        )


class TemplatesResponse(CamelModel):
    list: list[TemplateOut]


class SendTemplateRequest(CamelModel):
    """Отправка шаблона — единственный способ писать в закрытое окно."""

    template_id: uuid.UUID
    variables: list[str] = Field(
        default_factory=list,
        description="Значения плейсхолдеров {{1}}, {{2}}… по порядку",
        examples=[["Айгүл", "завтра в 11:00"]],
    )
