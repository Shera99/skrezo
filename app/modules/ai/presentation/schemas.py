"""Схемы модуля AI-ассистента."""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import Field, model_validator

from app.shared.api.schemas import CamelModel, NotNullFieldsModel
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
    dry_run: bool = Field(
        default=False,
        description=(
            "Пробный прогон: ответ бота, кнопки и шаблоны считаются и сохраняются в "
            "диалог, но в WhatsApp не уходят; нажатие кнопки не создаёт приём и не "
            "меняет запись. Диалог виден в кабинете клиники"
        ),
    )


class SimulatedMessageOut(CamelModel):
    role: str
    body: str
    sent: bool = Field(description="Ушло ли пациенту (в shadow ответы бота не уходят)")
    model: str | None = None


class SimulateInboundResponse(CamelModel):
    """Результат прогона: как отработал настоящий конвейер."""

    conversation_id: uuid.UUID
    outcome: str = Field(
        description=(
            "sent | shadow | draft | escalated | held | skipped | deferred | button; "
            "`held` — сотрудник уже закрыл передачу этого сообщения, повторной нет; "
            "при `dryRun` `sent` значит «ушло бы»"
        )
    )
    dry_run: bool = Field(
        default=False,
        description="Прогон был пробным: сообщения бота не отправлялись (`sent: false`)",
    )
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


class RewriteKnowledgeGapRequest(CamelModel):
    """Ответ администратора своими словами — на переписывание моделью."""

    answer: str = Field(
        min_length=3,
        max_length=4000,
        examples=["да парковка есть бесплатная во дворе заезд с токтогула"],
    )


class KnowledgeGapRewriteResponse(CamelModel):
    """Документ, предложенный моделью. Ничего не сохранено — подтверждают через `answer`."""

    title: str = Field(examples=["Парковка"])
    body: str = Field(
        examples=["У клиники есть бесплатная парковка во дворе, въезд с улицы Токтогула."]
    )
    category: str = Field(description="Предложенная категория", examples=["logistics"])
    notes: str | None = Field(
        default=None, description="Что модель изменила или на что обратить внимание"
    )
    mentions_price: bool = Field(
        description="В тексте осталась цена — сохранить не получится, уберите", examples=[False]
    )
    tokens_estimate: int = Field(description="Оценка токенов документа", examples=[24])


class KbVersionOut(CamelModel):
    """Снимок документа после одного сохранения."""

    version_no: int = Field(examples=[2])
    title: str
    category: str
    body: str
    lang: str
    is_active: bool
    action: str = Field(description="created | updated | rolled_back", examples=["updated"])
    note: str | None = Field(default=None, examples=["к версии 1"])
    actor_id: uuid.UUID | None = None
    created_at: datetime


class KbDocumentOut(CamelModel):
    """Документ базы знаний глазами редактора."""

    id: uuid.UUID
    title: str = Field(examples=["Как доехать"])
    category: str = Field(description="logistics | aftercare | faq | policy | payment")
    body: str
    lang: str = Field(description="ru | ky — язык диалогов, в которые уходит документ")
    is_active: bool = Field(description="Выключенный документ бот не видит, но он сохранён")
    version_no: int = Field(description="Номер текущей версии", examples=[3])
    tokens_estimate: int = Field(description="Оценка токенов документа (3 символа ≈ токен)")
    in_prompt: bool = Field(
        description="Помещается ли в промпт целиком: активные документы языка в порядке "
        "создания до потолка 30 000 токенов; что не поместилось — бот не знает"
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_doc(cls, d: Any, *, in_prompt: bool) -> KbDocumentOut:
        return cls(
            id=d.id,
            title=d.title,
            category=d.category,
            body=d.body,
            lang=d.lang,
            is_active=d.is_active,
            version_no=d.version_no,
            tokens_estimate=math.ceil((len(d.title) + len(d.body)) / 3),
            in_prompt=in_prompt,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )


class KbDocumentDetail(KbDocumentOut):
    versions: list[KbVersionOut] = Field(description="История, новые сверху")


class KbLangUsageOut(CamelModel):
    docs: int = Field(description="Активных документов на языке", examples=[12])
    tokens_estimate: int = Field(description="Оценка токенов активных документов", examples=[4100])


class KbUsageOut(CamelModel):
    """Сколько знания уходит в промпт — против порога полного контекста."""

    by_lang: dict[str, KbLangUsageOut] = Field(description="Ключи ru и ky")
    limit_tokens: int = Field(
        description="Порог: до него база уходит в промпт целиком", examples=[30000]
    )


class KbListResponse(CamelModel):
    list: list[KbDocumentOut]
    usage: KbUsageOut
    current_page: int
    page_size: int = 25
    total_count: int


class CreateKbDocumentRequest(CamelModel):
    """Новый документ — без цен, категория и язык из словаря."""

    title: str = Field(min_length=1, max_length=200, examples=["Как доехать"])
    category: str = Field(examples=["logistics"])
    body: str = Field(min_length=3, max_length=20_000)
    lang: str = Field(description="ru | ky", examples=["ru"])
    is_active: bool = True


class UpdateKbDocumentRequest(NotNullFieldsModel):
    """Правка документа: только изменяемые поля; `null` для них — 422."""

    NOT_NULL_FIELDS = ("title", "category", "body", "isActive")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    body: str | None = Field(default=None, min_length=3, max_length=20_000)
    is_active: bool | None = None


class KbRollbackRequest(CamelModel):
    version_no: int = Field(ge=1, description="К какой версии вернуться", examples=[1])


class RegressionCaseOut(CamelModel):
    """Кейс регрессии: вопрос пациента и ожидание, зависящее от вида."""

    id: uuid.UUID
    kind: str = Field(
        description="knowledge — ответ из документа; reply — одобренный ответ; "
        "refusal — не выдумывать; medical — медфильтр"
    )
    lang: str
    question: str
    expect: dict[str, Any] = Field(
        description="knowledge/reply: keywords (+reply); refusal: proposed, codes; medical: medical"
    )
    source: str = Field(
        description="knowledge_gap | approved_draft | edited_draft | incident | medical | manual"
    )
    origin: dict[str, Any] = Field(
        description="Ссылки: conversationId, messageId, gapId, kbDocumentId"
    )
    note: str | None = None
    is_active: bool
    last_result: str | None = Field(default=None, description="passed | failed | skipped")
    last_run_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_orm_case(cls, c: Any) -> RegressionCaseOut:
        return cls(
            id=c.id,
            kind=c.kind,
            lang=c.lang,
            question=c.question,
            expect=dict(c.expect or {}),
            source=c.source,
            origin=dict(c.origin or {}),
            note=c.note,
            is_active=c.is_active,
            last_result=c.last_result,
            last_run_at=c.last_run_at,
            created_at=c.created_at,
        )


class RegressionCaseCounts(CamelModel):
    total: int
    active: int
    failing: int = Field(description="Активных с последним итогом failed")


class RegressionCasesResponse(CamelModel):
    list: list[RegressionCaseOut]
    counts: RegressionCaseCounts
    current_page: int
    page_size: int = 25
    total_count: int


class CreateRegressionCaseRequest(CamelModel):
    """Ручной кейс — когда эталон известен без диалога."""

    kind: str = Field(description="knowledge | reply | refusal | medical", examples=["reply"])
    lang: str = Field(pattern="^(ru|ky)$", examples=["ru"])
    question: str = Field(min_length=3, max_length=1000, examples=["Есть ли у вас парковка?"])
    expected_reply: str | None = Field(
        default=None,
        max_length=2000,
        description="Эталон (knowledge/reply): ключевые слова возьмутся из него",
    )
    keywords: list[str] | None = Field(default=None, description="Или ключевые слова явно")
    medical: str | None = Field(
        default=None, description="Для medical: urgent | acute_pain | complaint | none"
    )
    proposed: str | None = Field(
        default=None,
        max_length=2000,
        description="Для refusal: выдуманный ответ, который валидатор обязан снять",
    )
    note: str | None = Field(default=None, max_length=500)


class UpdateRegressionCaseRequest(NotNullFieldsModel):
    NOT_NULL_FIELDS = ("isActive",)

    is_active: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class StartRegressionRunRequest(CamelModel):
    mode: str = Field(
        default="offline",
        pattern="^(offline|model)$",
        description="offline — без модели, бесплатно; model — сухой прогон модели по каждому кейсу",
    )


class RegressionRunOut(CamelModel):
    id: uuid.UUID
    mode: str
    status: str = Field(description="queued | running | done | failed")
    trigger: str
    prompt_version: str
    model_name: str | None = None
    total: int
    passed: int
    failed: int
    skipped: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    note: str | None = None
    created_at: datetime

    @classmethod
    def from_orm_run(cls, r: Any) -> RegressionRunOut:
        return cls(
            id=r.id,
            mode=r.mode,
            status=r.status,
            trigger=r.trigger,
            prompt_version=r.prompt_version,
            model_name=r.model_name,
            total=r.total,
            passed=r.passed,
            failed=r.failed,
            skipped=r.skipped,
            started_at=r.started_at,
            finished_at=r.finished_at,
            note=r.note,
            created_at=r.created_at,
        )


class RegressionResultOut(CamelModel):
    case_id: uuid.UUID
    kind: str
    question: str
    status: str = Field(description="passed | failed | skipped")
    expected: str
    actual: str
    note: str | None = None
    latency_ms: int | None = None


class RegressionRunDetail(RegressionRunOut):
    results: list[RegressionResultOut] = Field(description="Проваленные первыми")


class RegressionRunsResponse(CamelModel):
    list: list[RegressionRunOut]
    current_page: int
    page_size: int = 25
    total_count: int


class MetricsPeriodOut(CamelModel):
    """Фактически применённые границы периода в таймзоне клиники."""

    period: str | None = Field(
        default=None, description="Пресет: 7d | 1m | 3m | all; null — явные даты"
    )
    date_from: date | None = Field(default=None, description="null — за всё время")
    date_to: date
    timezone: str = Field(examples=["Asia/Bishkek"])


class AutonomyMetricsOut(CamelModel):
    """Сколько бот делает сам."""

    dialogs: int = Field(description="Диалогов, где пациент писал за период")
    replies_auto: int = Field(description="Ответов бота, ушедших пациенту без человека (auto)")
    replies_shadow: int = Field(
        description="Ответов, прошедших валидатор, но не отправленных (shadow)"
    )
    drafts_sent: int = Field(
        description="Черновиков, отправленных человеком (как есть или с правкой)"
    )
    drafts_edited: int
    drafts_discarded: int
    drafts_pending: int = Field(description="Ждут решения")
    escalations: int
    autonomy_share: float = Field(
        description="Доля без человека: auto / (auto + черновики + эскалации), 0..1",
        examples=[0.62],
    )
    edit_share: float = Field(
        description="Доля правок среди отправленных черновиков, 0..1", examples=[0.25]
    )


class QualityMetricsOut(CamelModel):
    validator_blocked: int = Field(
        description="Ответов, снятых валидатором (V01–V05; V09 — до 14.10.2026, дальше сигнал)"
    )
    needs_human: int = Field(description="Честных «не знаю» модели (V06) — ушли человеку")
    fact_errors: int = Field(
        description="Фактических ошибок: цена/время/врач не из фактов (V01–V03)"
    )
    low_confidence: int = Field(description="Ответов с низкой уверенностью модели")
    medical_urgent: int = Field(description="Неотложных по медфильтру")
    escalations_by_reason: dict[str, int] = Field(description="Эскалации по причинам §8.1")


class LearningMetricsOut(CamelModel):
    gaps_open: int = Field(description="Очередь «Не знает» сейчас")
    gaps_opened: int = Field(description="Новых пробелов за период")
    gaps_answered: int = Field(description="Выучил за период — ответили своими словами")
    gaps_dismissed: int
    avg_hours_to_answer: float = Field(
        description="Среднее время от первого вопроса до ответа, часов"
    )
    kb_documents_active: int
    kb_documents_added: int = Field(description="Документов базы знаний, добавленных за период")
    glossary_pending: int
    glossary_approved: int


class RegressionRunSummaryOut(CamelModel):
    id: uuid.UUID
    mode: str
    status: str
    passed: int
    failed: int
    skipped: int
    finished_at: datetime | None = None


class RegressionMetricsOut(CamelModel):
    cases_active: int
    cases_failing: int = Field(description="Активных кейсов с последним итогом failed")
    last_run: RegressionRunSummaryOut | None = None


class UsageMetricsOut(CamelModel):
    model_calls: int
    input_tokens: int
    output_tokens: int


class TrustGateOut(CamelModel):
    """Готовность к следующей ступени доверия (Ф7): «153 из 200» и чистая неделя."""

    mode: str = Field(description="Текущий режим: off | shadow | assisted | auto")
    approved: int = Field(description="Одобренных ответов за всё время", examples=[153])
    required: int = Field(examples=[200])
    progress: float = Field(description="approved / required, 0..1", examples=[0.765])
    fact_errors: int = Field(description="Ошибок V01–V03 за последние factErrorDays дней")
    fact_error_days: int = Field(examples=[7])
    ready: bool = Field(description="Одобрений достаточно и ошибок нет")


class MetricsSeriesPointOut(CamelModel):
    date: date
    replies_auto: int
    drafts_sent: int
    escalations: int
    gaps_opened: int
    gaps_answered: int


class AssistantMetricsResponse(CamelModel):
    """Экран «Обучение → показатели»: учится ли бот."""

    period: MetricsPeriodOut
    autonomy: AutonomyMetricsOut
    quality: QualityMetricsOut
    learning: LearningMetricsOut
    regression: RegressionMetricsOut
    usage: UsageMetricsOut
    trust_gate: TrustGateOut
    series: list[MetricsSeriesPointOut] = Field(
        description="По дням в таймзоне клиники; для all — последние 92 дня"
    )


class TrustRequirementOut(CamelModel):
    key: str = Field(examples=["approvals"])
    label: str = Field(examples=["одобренных ответов в режиме «Проверяю»"])
    value: int = Field(examples=[153])
    required: int = Field(examples=[200])
    met: bool


class TrustStepOut(CamelModel):
    level: str = Field(description="shadow | assisted | auto", examples=["assisted"])
    title: str = Field(examples=["Проверяю"])
    description: str
    current: bool
    ready: bool = Field(description="Все условия ступени выполнены")
    requirements: list[TrustRequirementOut]
    blockers: list[str] = Field(description="Чего не хватает — готовые подписи")


class TrustStatsOut(CamelModel):
    shadow_replies: int = Field(description="Ответов, прошедших валидатор в тени, за всё время")
    approvals: int = Field(description="Одобренных ответов за всё время")
    fact_errors: int = Field(description="Ошибок V01–V03 за factErrorDays дней")
    fact_error_days: int


class SkillOut(CamelModel):
    """Умение × клиника: потолок и действующий уровень."""

    key: str = Field(description="booking | changes", examples=["booking"])
    title: str = Field(examples=["Запись на приём"])
    description: str
    cap: str = Field(description="Потолок клиники: off | assisted | auto", examples=["assisted"])
    effective: str = Field(
        description="Действующий уровень = min(ступень, потолок): off | shadow | assisted | auto",
        examples=["assisted"],
    )


class TrustLadderResponse(CamelModel):
    """Экран «Настройки → Ассистент»: выключатель, лестница и матрица умений."""

    mode: AiMode
    enabled: bool = Field(description="false — ассистент выключен (off)")
    level: str | None = Field(default=None, description="Ступень; null, когда выключен")
    level_before_off: str | None = Field(
        default=None, description="Заработанная ступень до выключения — вернётся при включении"
    )
    changed_at: datetime | None = None
    reason: str | None = Field(
        default=None,
        description="Кто менял последним: manual (владелец) | health (мониторинг) | admin (Skrezo)",
    )
    stats: TrustStatsOut
    steps: list[TrustStepOut]
    next: TrustStepOut | None = Field(default=None, description="Следующая ступень и её условия")
    skills: list[SkillOut] = Field(
        default_factory=list,
        description="Матрица умений: запись, перенос/отмена — потолок и действующий уровень",
    )


class SetTrustRequest(CamelModel):
    """`level` ставит ступень (и включает); `enabled` — выключатель."""

    enabled: bool | None = Field(
        default=None,
        description="false — выключить, помня ступень; true — включить на заработанную",
    )
    level: str | None = Field(
        default=None, pattern="^(shadow|assisted|auto)$", examples=["assisted"]
    )
    skills: dict[str, str] | None = Field(
        default=None,
        description="Потолки умений: {booking?: off|assisted|auto, changes?: off|assisted|auto}",
        examples=[{"booking": "assisted"}],
    )

    @model_validator(mode="after")
    def _something_requested(self) -> SetTrustRequest:
        if self.enabled is None and self.level is None and not self.skills:
            raise ValueError("Укажите level, enabled или skills")
        return self
