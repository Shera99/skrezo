"""Схемы модуля WhatsApp (переписка с пациентами)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, tzinfo
from typing import Literal

from pydantic import ConfigDict, Field

from app.modules.whatsapp.application.use_cases.connection_status import ConnectionStatus
from app.modules.whatsapp.application.use_cases.prefill_template import TemplatePrefill
from app.modules.whatsapp.domain.connection import reconnect_message
from app.modules.whatsapp.domain.models import ConversationRow
from app.modules.whatsapp.domain.template_sources import VariableSource, normalize_sources
from app.modules.whatsapp.domain.templates import (
    template_title,
    unavailable_reason,
    variable_labels,
)
from app.modules.whatsapp.domain.window import is_window_open
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import AiConversationStatus, AiMessageRole, Lang, WhatsAppMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.whatsapp_history_message import WhatsAppHistoryMessage
from app.shared.db.models.whatsapp_template import WhatsAppTemplate


class ConversationListItem(CamelModel):
    """Строка списка диалогов — шапка, превью и состояние для вкладок кабинета."""

    id: uuid.UUID
    wa_phone: str = Field(description="Номер пациента (E.164)", examples=["+996700123456"])
    patient_id: uuid.UUID | None = Field(
        default=None, description="Глобальный пациент, если номер опознан"
    )
    patient_name: str | None = None
    contact_name: str | None = Field(
        default=None,
        description=(
            "Имя собеседника из адресной книги приложения WhatsApp Business клиники "
            "(номер подключён через Embedded Signup); показывать, пока нет `patientName`"
        ),
        examples=["Айгуль (чистка)"],
    )
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
            contact_name=r.contact_name,
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
    delivery_error: str | None = Field(
        default=None,
        description=(
            "Почему WhatsApp не доставил это сообщение — текст для сотрудника. Есть у "
            "исходящих, которые Meta отклонила при отправке (тогда `sentAt = null`) или "
            "позже статусом `failed`. null — доставка не падала"
        ),
        examples=[
            "WhatsApp не доставил сообщение: номер пациента не в списке разрешённых "
            "получателей — WhatsApp клиники подключён в тестовом режиме Meta"
        ],
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
            delivery_error=m.delivery_error,
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
    title: str = Field(
        description=(
            "Понятное название для интерфейса: у общих шаблонов платформы его задаёт "
            "Skrezo, у шаблонов клиники — имя из Meta"
        ),
        examples=["Напоминание о приёме"],
    )
    lang: str = Field(description="Код языка Meta", examples=["ru"])
    category: str = Field(examples=["utility"])
    body_preview: str = Field(description="Текст тела с плейсхолдерами {{1}}, {{2}}…")
    variables: list[str] = Field(
        description="Плейсхолдеры тела по порядку — столько переменных ждёт отправка"
    )
    variable_labels: list[str] = Field(
        description=(
            "Подписи переменных по порядку плейсхолдеров («Имя», «Дата и время»); без "
            "своей подписи — подпись источника значения («Имя пациента»), без "
            "источника — сам плейсхолдер `{{1}}`"
        ),
        examples=[["Имя", "Дата и время"]],
    )
    variable_sources: list[VariableSource] = Field(
        description=(
            "Откуда кабинет берёт значение каждой переменной при отправке "
            "(`GET …/templates/{templateId}/prefill`), по порядку плейсхолдеров; "
            "`manual` — сотрудник вводит сам. Список источников — "
            "`GET /clinic/whatsapp/templates/variable-sources`"
        ),
        examples=[["patient_name", "appointment_date"]],
    )
    status: str = Field(
        description="approved | pending | rejected | paused | disabled; отправка — только approved"
    )
    status_changed_at: datetime | None = Field(
        default=None,
        description="Когда синхронизация увидела текущий статус; null — до появления поля",
    )
    unavailable_reason: str | None = Field(
        default=None,
        description=(
            "Почему шаблон нельзя отправить, на языке запроса (`?lang`, Accept-Language); "
            "null — можно"
        ),
        examples=["На проверке в Meta с 20 сентября"],
    )
    shared: bool = Field(description="Общий шаблон платформы (доступен всем клиникам)")

    @classmethod
    def from_orm_template(
        cls, t: WhatsAppTemplate, *, lang: Lang = Lang.RU, tz: tzinfo = UTC
    ) -> TemplateOut:
        return cls(
            id=t.id,
            meta_name=t.meta_name,
            title=template_title(t.meta_name, t.title),
            lang=t.lang,
            category=t.category,
            body_preview=t.body_preview,
            variables=[str(v) for v in t.variables],
            variable_labels=variable_labels(
                t.variables, t.variable_labels, t.variable_sources, lang
            ),
            variable_sources=normalize_sources(t.variables, t.variable_sources),
            status=t.status,
            status_changed_at=t.status_changed_at,
            unavailable_reason=unavailable_reason(t.status, t.status_changed_at, lang, tz),
            shared=t.clinic_id is None,
        )


class TemplateLabelsRequest(CamelModel):
    """Название, подписи и источники переменных шаблона.

    Общий шаблон платформы правит Skrezo (`PATCH /admin/whatsapp/templates/{id}`),
    свой шаблон — клиника (`PATCH /clinic/whatsapp/templates/{id}`).
    """

    title: str | None = Field(
        default=None,
        max_length=120,
        description="Понятное название для кабинетов клиник; null — вернуть имя из Meta",
        examples=["Напоминание о приёме"],
    )
    variable_labels: list[str] | None = Field(
        default=None,
        description=(
            "Подписи переменных по порядку плейсхолдеров — ровно столько, сколько "
            "переменных у шаблона; не передано — не меняются"
        ),
        examples=[["Имя", "Дата и время"]],
    )
    variable_sources: list[VariableSource] | None = Field(
        default=None,
        description=(
            "Источник значения каждой переменной по порядку плейсхолдеров — ровно "
            "столько, сколько переменных; не передано — не меняются"
        ),
        examples=[["patient_name", "clinic_name"]],
    )


class TemplatesResponse(CamelModel):
    list: list[TemplateOut]


class VariableSourceOut(CamelModel):
    """Источник значения переменной — пункт выбора в кабинете."""

    key: VariableSource = Field(examples=["patient_name"])
    label: str = Field(description="Подпись на языке запроса", examples=["Имя пациента"])


class VariableSourcesResponse(CamelModel):
    list: list[VariableSourceOut]


class PrefilledVariableOut(CamelModel):
    """Переменная шаблона с найденным значением."""

    placeholder: str = Field(examples=["{{1}}"])
    label: str = Field(description="Подпись поля", examples=["Имя пациента"])
    source: VariableSource = Field(examples=["patient_name"])
    value: str | None = Field(
        description=(
            "Значение для этого диалога; null — не нашлось (нет карточки пациента, "
            "будущей записи, долга) или источник `manual`: поле заполняет сотрудник"
        ),
        examples=["Айбек"],
    )


class TemplatePrefillResponse(CamelModel):
    """Предзаполненные переменные шаблона — подставить в поля перед отправкой."""

    template_id: uuid.UUID
    title: str = Field(examples=["Приветствие"])
    variables: list[PrefilledVariableOut]
    complete: bool = Field(description="Все значения найдены — шаблон можно отправить без ввода")

    @classmethod
    def from_prefill(cls, prefill: TemplatePrefill) -> TemplatePrefillResponse:
        return cls(
            template_id=prefill.template_id,
            title=prefill.title,
            variables=[
                PrefilledVariableOut(
                    placeholder=v.placeholder, label=v.label, source=v.source, value=v.value
                )
                for v in prefill.variables
            ],
            complete=prefill.complete,
        )


class SendTemplateRequest(CamelModel):
    """Отправка шаблона — единственный способ писать в закрытое окно."""

    template_id: uuid.UUID
    variables: list[str] = Field(
        default_factory=list,
        description="Значения плейсхолдеров {{1}}, {{2}}… по порядку",
        examples=[["Айгүл", "завтра в 11:00"]],
    )


# ── Номер клиники: самостоятельное подключение (Embedded Signup) ────────────


class EmbeddedSignupConfigOut(CamelModel):
    """Параметры окна Meta для кабинета; все значения публичные, секретов нет."""

    available: bool = Field(
        description=(
            "Можно ли подключить номер из кабинета. `false` — Skrezo ещё не настроил "
            "приложение Meta: кнопку подключения не показывать"
        )
    )
    app_id: str | None = Field(
        default=None,
        description="ID приложения Meta для `FB.init({appId})`",
        examples=["4438887573096444"],
    )
    config_id: str | None = Field(
        default=None,
        description="ID конфигурации Facebook Login for Business для `FB.login({config_id})`",
        examples=["1234567890123456"],
    )
    graph_api_version: str | None = Field(
        default=None, description="Версия Graph API для `FB.init({version})`", examples=["v23.0"]
    )
    feature_type: str = Field(
        description=(
            "`extras.featureType` для `FB.login`: номер остаётся в приложении "
            "WhatsApp Business и работает в Skrezo одновременно"
        ),
        examples=["whatsapp_business_app_onboarding"],
    )
    session_info_version: str = Field(
        description="`extras.sessionInfoVersion` для `FB.login`", examples=["3"]
    )


class WhatsAppReconnectOut(CamelModel):
    """Номер перестал работать у нас — клинике нужно пройти подключение заново."""

    since: datetime
    reason: str = Field(
        description=(
            "`partner_removed` — доступ отозван в Meta; `account_offboarded` — номер "
            "перерегистрирован в приложении на другом телефоне; `account_deleted`; "
            "`token_invalid` — Meta не принимает доступ клиники"
        ),
        examples=["partner_removed"],
    )
    message: str = Field(examples=["Доступ Skrezo к аккаунту WhatsApp Business отозван в Meta"])


class WhatsAppAppSyncOut(CamelModel):
    """Перенос данных приложения WhatsApp Business: контакты и история переписки."""

    contacts_requested_at: datetime | None = Field(
        default=None, description="Когда запрошены контакты приложения"
    )
    history_requested_at: datetime | None = Field(
        default=None, description="Когда запрошена история переписки"
    )
    history_progress: int | None = Field(
        default=None, description="Ход импорта истории по данным Meta, 0–100", examples=[55]
    )
    history_synced_at: datetime | None = Field(
        default=None, description="История загружена полностью"
    )
    history_error: str | None = Field(
        default=None,
        description="Почему истории не будет (клиника отказалась делиться в приложении и т.п.)",
    )


class WhatsAppConnectionResponse(CamelModel):
    """Номер WhatsApp клиники и параметры окна подключения Meta."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "connected": True,
                "phoneNumberId": "106540352242922",
                "displayPhone": "+996700123456",
                "verifiedName": "Стоматология Улыбка",
                "wabaId": "102290129340398",
                "mode": "coexistence",
                "connectedVia": "embedded_signup",
                "connectedAt": "2026-10-04T09:15:00Z",
                "qualityRating": "GREEN",
                "statusCheckedAt": "2026-10-04T09:15:00Z",
                "needsReconnect": None,
                "sync": {
                    "contactsRequestedAt": "2026-10-04T09:15:02Z",
                    "historyRequestedAt": "2026-10-04T09:15:02Z",
                    "historyProgress": 100,
                    "historySyncedAt": "2026-10-04T09:21:40Z",
                    "historyError": None,
                },
                "signup": {
                    "available": True,
                    "appId": "4438887573096444",
                    "configId": "1234567890123456",
                    "graphApiVersion": "v23.0",
                    "featureType": "whatsapp_business_app_onboarding",
                    "sessionInfoVersion": "3",
                },
            }
        }
    )

    connected: bool = Field(description="Номер подключён и принимает сообщения")
    phone_number_id: str | None = Field(default=None, description="ID номера в Meta")
    display_phone: str | None = Field(default=None, examples=["+996700123456"])
    verified_name: str | None = Field(
        default=None, description="Отображаемое имя бизнеса, одобренное Meta"
    )
    waba_id: str | None = Field(default=None, description="WhatsApp Business Account клиники")
    mode: WhatsAppMode | None = Field(
        default=None,
        description="`coexistence` — номер работает и в приложении WhatsApp Business; `api_only`",
    )
    connected_via: Literal["manual", "embedded_signup"] | None = Field(
        default=None,
        description="`embedded_signup` — подключила клиника сама; `manual` — команда Skrezo",
    )
    connected_at: datetime | None = None
    quality_rating: str | None = Field(
        default=None, description="Рейтинг качества номера у Meta: GREEN / YELLOW / RED"
    )
    status_checked_at: datetime | None = Field(
        default=None, description="Когда состояние номера последний раз спрашивали у Meta"
    )
    needs_reconnect: WhatsAppReconnectOut | None = Field(
        default=None, description="Заполнено — номер не работает, нужно подключить заново"
    )
    sync: WhatsAppAppSyncOut | None = Field(
        default=None,
        description="Перенос контактов и истории (только номер, подключённый клиникой сама)",
    )
    signup: EmbeddedSignupConfigOut

    @classmethod
    def from_status(cls, status: ConnectionStatus) -> WhatsAppConnectionResponse:
        b = status.binding
        signup = EmbeddedSignupConfigOut(
            available=status.signup.available,
            app_id=status.signup.app_id,
            config_id=status.signup.config_id,
            graph_api_version=status.signup.graph_api_version,
            feature_type=status.signup.feature_type,
            session_info_version=status.signup.session_info_version,
        )
        if b is None or not b.active:
            return cls(connected=False, signup=signup)
        reconnect = (
            WhatsAppReconnectOut(
                since=b.needs_reconnect_at,
                reason=b.needs_reconnect_reason or "token_invalid",
                message=reconnect_message(b.needs_reconnect_reason) or "",
            )
            if b.needs_reconnect_at is not None
            else None
        )
        self_service = b.connected_via == "embedded_signup"
        return cls(
            connected=True,
            phone_number_id=b.phone_number_id,
            display_phone=b.display_phone,
            verified_name=b.verified_name,
            waba_id=b.waba_id,
            mode=b.mode,
            connected_via="embedded_signup" if self_service else "manual",
            connected_at=b.connected_at,
            quality_rating=b.quality_rating,
            status_checked_at=b.status_checked_at,
            needs_reconnect=reconnect,
            sync=(
                WhatsAppAppSyncOut(
                    contacts_requested_at=b.contacts_sync_requested_at,
                    history_requested_at=b.history_sync_requested_at,
                    history_progress=b.history_progress,
                    history_synced_at=b.history_synced_at,
                    history_error=b.history_error,
                )
                if self_service
                else None
            ),
            signup=signup,
        )


class EmbeddedSignupRequest(CamelModel):
    """Что кабинет получил из окна Meta — отправить сразу: код живёт 30 секунд."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"code": "AQBx…", "wabaId": "102290129340398"}}
    )

    code: str = Field(
        min_length=1,
        max_length=4096,
        description="`response.authResponse.code` из колбэка `FB.login`",
    )
    waba_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^\d+$",
        description="`data.waba_id` из события `WA_EMBEDDED_SIGNUP` (FINISH…)",
        examples=["102290129340398"],
    )
    phone_number_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^\d+$",
        description=(
            "`data.phone_number_id` из события — есть у нового номера; у номера из "
            "приложения WhatsApp Business его нет, номер находится по WABA"
        ),
        examples=["106540352242922"],
    )


class HistoryMessageOut(CamelModel):
    """Сообщение архива переписки из приложения WhatsApp Business."""

    id: int
    direction: Literal["in", "out"] = Field(
        description="`in` — написал собеседник, `out` — клиника из приложения"
    )
    body: str
    media_type: str | None = Field(
        default=None,
        description="Тип вложения по словарю Meta; `media_placeholder` — медиа без файла",
    )
    status: str | None = Field(
        default=None, description="Статус из истории: DELIVERED / READ / PLAYED", examples=["READ"]
    )
    sent_at: datetime

    @classmethod
    def from_model(cls, m: WhatsAppHistoryMessage) -> HistoryMessageOut:
        return cls(
            id=m.id,
            direction="out" if m.direction == "out" else "in",
            body=m.body,
            media_type=m.media_type,
            status=m.status,
            sent_at=m.sent_at,
        )


class HistoryPageResponse(CamelModel):
    """Страница архива: от свежих к старым."""

    items: list[HistoryMessageOut]
    next_before_id: int | None = Field(
        default=None,
        description="Передать в `beforeId`, чтобы загрузить старше; `null` — архив кончился",
    )
