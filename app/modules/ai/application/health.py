"""Мониторинг здоровья интеграции ассистента (§13.5).

Все отказы этой системы **молчаливые**: провайдер модели лёг, токен WhatsApp
истёк, Meta отключила подписку после серии неудачных доставок — ассистент
просто перестаёт работать, ошибка нигде не всплывает, и узнаём мы об этом от
клиники через день. Это худший способ узнать.

Два правила раздела соблюдаются буквально:

1. **Алерты идут нам, а не клинике.** Клиника не может починить наш токен,
   сообщение об этом её только напугает. Ей достаётся деградация поведения:
   при недоступной модели режим ``auto`` автоматически опускается до
   ``assisted`` — ответы уходят через администратора.
2. Проверка дешёвая и частая (раз в 5 минут), поэтому пробный вызов модели
   делается один на всю платформу, а не на каждую клинику.

Уроки октября 2026 (клиника неделю отвечала только через одобрение, пациенты
ждали часами):

* понижение — после ``AI_HEALTH_DOWNGRADE_AFTER`` неудач **подряд**, а не
  после одной: мигнувший провайдер не должен менять режим клиник;
* понижение, сделанное проверкой, проверка же и снимает, когда модель ожила
  (``ai_mode_reason = "health"`` → снова ``auto``) — после стольких же
  здоровых проверок подряд, чтобы не гонять клиники туда-обратно. Режим,
  выставленный человеком, не трогаем никогда;
* проба тратит ту же квоту, что и пациенты, поэтому сначала смотрим на живой
  трафик: недавний успешный вызов — модель жива, разомкнутый предохранитель —
  лежит; пробуем, только когда трафика нет;
* «пациент без ответа» — отдельный алерт: молчание по любой причине (режим,
  передача, отказ WhatsApp) должно всплывать у нас, а не у клиники через день.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.push.application.quiet_service import QuietService
from app.modules.whatsapp.application.services.credentials import client_for
from app.shared.ai import AiMessagePart, AiProvider, AiProviderError
from app.shared.ai.resilience import ALIVE_KEY, OPEN_KEY
from app.shared.db.enums import AiConversationStatus, AiMessageRole, AiMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.notifications.notifier import Notifier
from app.shared.speech import SpeechError, SpeechProvider
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

#: Пробный вызов модели: ответ не важен, важен сам факт ответа.
_PROBE_SYSTEM = "Ответь одним словом: ок."
_PROBE_TOKENS = 16

#: Клиника считается живой, если за неделю пришло хотя бы столько сообщений;
#: молчание живой клиники дольше порога — повод заподозрить тихий отказ.
_ACTIVE_MIN_MESSAGES = 10
_SILENCE_ALERT = timedelta(hours=12)
_ACTIVITY_WINDOW = timedelta(days=7)

#: Счётчик неудачных проверок модели подряд; живёт час — старые неудачи не
#: должны складываться с сегодняшними.
_FAILURES_KEY = "ai:health:model_failures"
_FAILURES_TTL_SECONDS = 3600
#: Здоровых проверок подряд — возврат в auto только после стольких же, сколько
#: нужно для понижения: иначе провайдер с редкими 429 гонял бы клиники
#: туда-обратно каждые 15 минут.
_HEALTHY_KEY = "ai:health:model_healthy"

#: Кого из ждущих пациентов уже показали команде: алерт — один на сообщение
#: (или на передачу).
_UNANSWERED_KEY = "ai:health:unanswered:{kind}:{ref_id}"
_UNANSWERED_TTL_SECONDS = 2 * 24 * 3600
#: Пациент старше окна WhatsApp — писать ему всё равно можно только шаблоном.
_UNANSWERED_WINDOW = timedelta(hours=24)
#: Сколько строк алерта показывать — дальше «и ещё N».
_UNANSWERED_LINES = 10
#: Что ждёт пациент — подпись строки алерта.
_UNANSWERED_KINDS = {
    "message": "сообщение без ответа",
    "handoff": "передача без ответа сотрудника",
}

#: Чьё сообщение считается ответом клиники пациенту.
_CLINIC_ROLES = (AiMessageRole.BOT, AiMessageRole.ADMIN, AiMessageRole.SYSTEM)


@dataclass(frozen=True, slots=True)
class UnansweredPatient:
    """Пациент, который ждёт ответа клиники дольше порога.

    ``kind``: ``message`` — его сообщение осталось без ответа; ``handoff`` —
    бот передал разговор человеку (и сказал об этом пациенту), а сотрудник
    так и не написал. ``ref_id`` — сообщение или передача, по ним дедуп.
    """

    kind: str
    ref_id: str
    clinic_id: uuid.UUID
    clinic_name: str
    conversation_id: uuid.UUID
    phone: str
    status: str
    waited_minutes: int


@dataclass
class HealthReport:
    """Что проверили и что нашли."""

    model_ok: bool = True
    speech_ok: bool | None = None  # None — распознавание не настроено
    tokens_failed: list[str] = field(default_factory=list)
    silent_clinics: list[str] = field(default_factory=list)
    downgraded: int = 0
    #: Клиники, которые проверка сама опустила, а теперь вернула в ``auto``.
    restored: int = 0
    #: Неудач модели подряд (без Redis счёта нет — сразу порог).
    model_failures: int = 0
    #: Пациенты, ждущие ответа дольше порога (новые — те, о ком ещё не писали).
    unanswered: list[UnansweredPatient] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.model_ok and not self.tokens_failed and self.speech_ok is not False


class AiHealthService:
    """Проверки §13.5 и автоматическая деградация режима."""

    def __init__(
        self,
        session: AsyncSession,
        provider: AiProvider | None,
        wa_client: WhatsAppCloudClient,
        speech: SpeechProvider | None = None,
        *,
        redis: Redis | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._wa = wa_client
        self._speech = speech
        #: Счётчик неудач подряд, отметки живого трафика, дедуп алертов. Без
        #: Redis каждая неудача считается решающей (как раньше), а алерт о
        #: ждущем пациенте повторяется на каждой проверке.
        self._redis = redis
        #: Чат команды Skrezo; ``None`` — алерты только в лог.
        self._notifier = notifier

    async def check(self, *, tokens: bool = True) -> HealthReport:
        """Все проверки; ``tokens=False`` — без обхода номеров (см. ``check_tokens``)."""
        report = HealthReport()
        report.model_ok = await self._model_ok()
        if report.model_ok:
            if await self._count_healthy() >= settings.ai_health_downgrade_after:
                report.restored = await self._restore_auto_clinics()
        else:
            report.model_failures = await self._count_failure()
            if report.model_failures >= settings.ai_health_downgrade_after:
                # Клиника узнаёт о поломке не алертом, а поведением: ответы
                # начинают уходить через администратора (§13.5, правило 1).
                report.downgraded = await self._downgrade_auto_clinics()
        report.speech_ok = await self._speech_ok()
        report.tokens_failed = await self._failed_tokens() if tokens else []
        report.silent_clinics = await self._silent_clinics()
        report.unanswered = await self._new_unanswered()

        if not report.healthy:
            logger.error(
                "ai_health_alert",
                model_ok=report.model_ok,
                model_failures=report.model_failures,
                speech_ok=report.speech_ok,
                tokens_failed=report.tokens_failed,
                downgraded=report.downgraded,
            )
        for clinic_id in report.silent_clinics:
            logger.warning("ai_health_silence", clinic_id=clinic_id)
        await self._alert_team(report)
        return report

    # ── Отдельные проверки ──────────────────────────────────────────────────

    async def _model_ok(self) -> bool:
        if self._provider is None:
            return True  # ассистент не настроен — ломаться нечему
        live = await self._live_signal()
        if live is not None:
            return live
        try:
            result = await self._provider.complete(
                system=_PROBE_SYSTEM,
                parts=[AiMessagePart(role="user", text="ок?")],
                tools=[],
                temperature=0.0,
                max_tokens=_PROBE_TOKENS,
            )
        except AiProviderError as exc:
            logger.warning("ai_health_model_down", error=str(exc))
            return False
        return bool((result.text or "").strip()) or result.tool_call is not None

    async def _live_signal(self) -> bool | None:
        """Что о модели говорит живой трафик; ``None`` — молчит, нужна проба.

        Разомкнутый предохранитель — живые вызовы падают подряд: модель лежит,
        и проба только добавила бы ещё один отказ. Недавний успешный вызов —
        модель жива, тратить на пробу квоту незачем.
        """
        if self._redis is None:
            return None
        try:
            if await self._redis.exists(OPEN_KEY):
                return False
            if await self._redis.exists(ALIVE_KEY):
                return True
        except RedisError as exc:
            logger.warning("ai_health_redis_failed", error=str(exc))
        return None

    async def _count_failure(self) -> int:
        """Ещё одна неудачная проверка; вернуть, сколько их подряд.

        Без Redis (или при его сбое) счёта нет — неудача сразу решающая, как
        до порога.
        """
        return await self._streak(_FAILURES_KEY, reset=_HEALTHY_KEY)

    async def _count_healthy(self) -> int:
        """Ещё одна здоровая проверка; вернуть, сколько их подряд."""
        return await self._streak(_HEALTHY_KEY, reset=_FAILURES_KEY)

    async def _streak(self, key: str, *, reset: str) -> int:
        """Прибавить к серии ``key`` и оборвать противоположную ``reset``."""
        if self._redis is None:
            return settings.ai_health_downgrade_after
        try:
            count = int(await self._redis.incr(key))
            await self._redis.expire(key, _FAILURES_TTL_SECONDS)
            await self._redis.delete(reset)
        except RedisError as exc:
            logger.warning("ai_health_redis_failed", error=str(exc))
            return settings.ai_health_downgrade_after
        return count

    async def _speech_ok(self) -> bool | None:
        """Пробная расшифровка. ``None`` — распознавание не настроено.

        Отправляем заведомо пустую запись: нам важна доступность сервиса, а не
        качество — пустой результат при живом сервисе ошибкой не считается.
        """
        if self._speech is None:
            return None
        try:
            await self._speech.transcribe(b"", "audio/ogg")
        except SpeechError as exc:
            logger.warning("ai_health_speech_down", error=str(exc))
            return False
        return True

    async def check_tokens(self) -> list[str]:
        """Обход номеров отдельным, редким заданием (Ф8).

        Проверка — вызов Graph API на каждую привязку; раз в 5 минут это
        43 тысячи вызовов в сутки одним токеном на 150 клиник, а раз в час —
        3,6 тысячи. Отозванный токен виден и через час: до этого сбой всё
        равно проявится в отправке.
        """
        failed = await self._failed_tokens()
        if failed:
            logger.error("wa_token_alert", tokens_failed=failed)
        return failed

    async def _failed_tokens(self) -> list[str]:
        """Номера, для которых Graph API больше не принимает наш токен."""
        bindings = (
            await self._session.execute(
                select(ClinicWhatsApp).where(ClinicWhatsApp.active.is_(True))
            )
        ).scalars()
        failed: list[str] = []
        for binding in bindings:
            if not await client_for(self._wa, binding).check_token(binding.phone_number_id):
                failed.append(binding.phone_number_id)
        return failed

    async def _silent_clinics(self) -> list[str]:
        """Живые клиники, от которых давно нет входящих, — возможен тихий отказ."""
        now = datetime.now(UTC)
        rows = (
            await self._session.execute(
                select(
                    AiMessage.clinic_id,
                    func.count().label("total"),
                    func.max(AiMessage.created_at).label("last_at"),
                )
                .where(
                    AiMessage.role == AiMessageRole.PATIENT,
                    AiMessage.created_at >= now - _ACTIVITY_WINDOW,
                )
                .group_by(AiMessage.clinic_id)
            )
        ).all()
        return [
            str(clinic_id)
            for clinic_id, total, last_at in rows
            if total >= _ACTIVE_MIN_MESSAGES and last_at < now - _SILENCE_ALERT
        ]

    async def _downgrade_auto_clinics(self) -> int:
        """``auto`` → ``assisted``, пока модель недоступна.

        Причина ``health`` — метка «опустила проверка»: по ней же проверка
        вернёт клинику в ``auto``, когда модель оживёт
        (:meth:`_restore_auto_clinics`). Режим, выставленный человеком, причину
        перезаписывает — и автоматически его уже никто не тронет.
        """
        result = await self._session.execute(
            update(Clinic)
            .where(Clinic.ai_mode == AiMode.AUTO)
            .values(ai_mode=AiMode.ASSISTED, ai_mode_reason="health", ai_mode_changed_at=func.now())
            .returning(Clinic.id)
        )
        changed = list(result.scalars())
        if changed:
            await self._session.commit()
            logger.error("ai_health_downgraded", clinics=[str(c) for c in changed])
        return len(changed)

    async def _restore_auto_clinics(self) -> int:
        """Вернуть в ``auto`` клиники, которые опустила сама проверка.

        Раньше возврат был только ручным, и после мигнувшего провайдера
        клиника неделями отвечала через одобрение администратора: пациенты
        ждали часами, а передачи человеку уходили без единого слова пациенту.
        """
        result = await self._session.execute(
            update(Clinic)
            .where(Clinic.ai_mode == AiMode.ASSISTED, Clinic.ai_mode_reason == "health")
            .values(
                ai_mode=AiMode.AUTO,
                ai_mode_reason="health_restored",
                ai_mode_changed_at=func.now(),
            )
            .returning(Clinic.id)
        )
        changed = list(result.scalars())
        if changed:
            await self._session.commit()
            logger.info("ai_health_restored", clinics=[str(c) for c in changed])
        return len(changed)

    async def _new_unanswered(self) -> list[UnansweredPatient]:
        """Пациенты, ждущие клинику дольше порога, о которых команда ещё не знает.

        Два правила, оба — только в ``assisted``/``auto`` (в выключенном и
        теневом режиме отвечает человек, это дело клиники) и только когда
        клиника открыта (ночью пациенту уже сказано, когда ответят; утром, если
        ответа так и нет, алерт придёт на первой же проверке):

        * **сообщение** — после последнего отправленного сообщения клиники
          (бот, сотрудник, служебное) пациент написал, и ПЕРВОЕ такое сообщение
          старше порога. Именно первое: пациент, который каждые 20 минут пишет
          «алло?», иначе не попадал бы в алерт никогда;
        * **передача** — бот передал разговор человеку и сказал об этом
          пациенту (фраза передачи — ответ для первого правила), а сотрудник за
          порог так и не написал. Это ровно инцидент 22–31.10.2026.
        """
        now = datetime.now(UTC)
        threshold = now - timedelta(minutes=settings.ai_unanswered_alert_minutes)
        since = now - _UNANSWERED_WINDOW
        found = await self._unanswered_handoffs(since, threshold, now)
        seen = {p.conversation_id for p in found}
        for patient in await self._unanswered_messages(since, threshold, now):
            if patient.conversation_id not in seen:
                found.append(patient)
        open_now: dict[uuid.UUID, bool] = {}
        quiet = QuietService(self._session)
        fresh: list[UnansweredPatient] = []
        for patient in found:
            if patient.clinic_id not in open_now:
                open_now[patient.clinic_id] = not (
                    await quiet.moment(patient.clinic_id, now)
                ).quiet
            if open_now[patient.clinic_id] and await self._first_alert(patient):
                fresh.append(patient)
        return fresh

    async def _unanswered_messages(
        self, since: datetime, threshold: datetime, now: datetime
    ) -> list[UnansweredPatient]:
        """Первое сообщение пациента после последнего ответа клиники — старше порога.

        Порядок сообщений — по ``id``, а не по часам: так работает индекс
        ``(conversation_id, id)`` и не смешиваются часы приложения и БД.
        Ответы старше окна не нужны: все сообщения окна идут после них.
        """
        answered = (
            select(
                AiMessage.conversation_id.label("conversation_id"),
                func.max(AiMessage.id).label("answer_id"),
            )
            .where(
                AiMessage.role.in_(_CLINIC_ROLES),
                AiMessage.sent_at.is_not(None),
                AiMessage.created_at >= since,
            )
            .group_by(AiMessage.conversation_id)
            .subquery()
        )
        waiting = (
            select(
                AiMessage.conversation_id.label("conversation_id"),
                func.min(AiMessage.id).label("message_id"),
                func.min(AiMessage.created_at).label("at"),
            )
            .outerjoin(answered, answered.c.conversation_id == AiMessage.conversation_id)
            .where(
                AiMessage.role == AiMessageRole.PATIENT,
                AiMessage.created_at >= since,
                AiMessage.id > func.coalesce(answered.c.answer_id, 0),
            )
            .group_by(AiMessage.conversation_id)
            .subquery()
        )
        rows = (
            await self._session.execute(
                select(
                    Clinic.id,
                    Clinic.name,
                    AiConversation.id,
                    waiting.c.message_id,
                    AiConversation.wa_phone,
                    AiConversation.status,
                    waiting.c.at,
                )
                .join(AiConversation, AiConversation.id == waiting.c.conversation_id)
                .join(Clinic, Clinic.id == AiConversation.clinic_id)
                .where(
                    waiting.c.at <= threshold,
                    Clinic.ai_mode.in_((AiMode.ASSISTED, AiMode.AUTO)),
                )
                .order_by(waiting.c.at)
            )
        ).all()
        return [
            UnansweredPatient(
                kind="message",
                ref_id=str(message_id),
                clinic_id=clinic_id,
                clinic_name=clinic_name,
                conversation_id=conversation_id,
                phone=phone,
                status=status.value,
                waited_minutes=int((now - at).total_seconds() // 60),
            )
            for clinic_id, clinic_name, conversation_id, message_id, phone, status, at in rows
        ]

    async def _unanswered_handoffs(
        self, since: datetime, threshold: datetime, now: datetime
    ) -> list[UnansweredPatient]:
        """Последняя открытая передача диалога старше порога, а сотрудник молчит.

        «Отвечаю сам» не в счёт: сотрудник сам взял разговор и в курсе.
        """
        latest = (
            select(
                AiEscalation.id.label("id"),
                AiEscalation.conversation_id.label("conversation_id"),
                AiEscalation.reason.label("reason"),
                AiEscalation.created_at.label("created_at"),
            )
            .where(AiEscalation.resolved_at.is_(None), AiEscalation.created_at >= since)
            .distinct(AiEscalation.conversation_id)
            .order_by(AiEscalation.conversation_id, AiEscalation.created_at.desc())
            .subquery()
        )
        staff_replied = (
            select(AiMessage.id)
            .where(
                AiMessage.conversation_id == latest.c.conversation_id,
                AiMessage.role == AiMessageRole.ADMIN,
                AiMessage.created_at >= latest.c.created_at,
            )
            .exists()
        )
        rows = (
            await self._session.execute(
                select(
                    Clinic.id,
                    Clinic.name,
                    AiConversation.id,
                    latest.c.id,
                    AiConversation.wa_phone,
                    AiConversation.status,
                    latest.c.created_at,
                )
                .join(AiConversation, AiConversation.id == latest.c.conversation_id)
                .join(Clinic, Clinic.id == AiConversation.clinic_id)
                .where(
                    AiConversation.status == AiConversationStatus.ESCALATED,
                    Clinic.ai_mode.in_((AiMode.ASSISTED, AiMode.AUTO)),
                    latest.c.reason != "manual_takeover",
                    latest.c.created_at <= threshold,
                    ~staff_replied,
                )
                .order_by(latest.c.created_at)
            )
        ).all()
        return [
            UnansweredPatient(
                kind="handoff",
                ref_id=str(escalation_id),
                clinic_id=clinic_id,
                clinic_name=clinic_name,
                conversation_id=conversation_id,
                phone=phone,
                status=status.value,
                waited_minutes=int((now - at).total_seconds() // 60),
            )
            for clinic_id, clinic_name, conversation_id, escalation_id, phone, status, at in rows
        ]

    async def _first_alert(self, patient: UnansweredPatient) -> bool:
        """Впервые ли команда узнаёт об этом сообщении или передаче (дедуп в Redis)."""
        if self._redis is None:
            return True
        try:
            return bool(
                await self._redis.set(
                    _UNANSWERED_KEY.format(kind=patient.kind, ref_id=patient.ref_id),
                    "1",
                    nx=True,
                    ex=_UNANSWERED_TTL_SECONDS,
                )
            )
        except RedisError as exc:
            logger.warning("ai_health_redis_failed", error=str(exc))
            return True

    async def _alert_team(self, report: HealthReport) -> None:
        """Сообщить команде Skrezo о смене режимов и ждущих пациентах.

        В чат — только то, что требует действия: смена режима клиник (в обе
        стороны) и пациенты без ответа. Остальное — в лог, как раньше.
        """
        lines: list[str] = []
        if report.downgraded:
            lines.append(
                f"⚠️ Модель недоступна {report.model_failures} проверок подряд: "
                f"клиник переведено в «Проверяю» — {report.downgraded}."
            )
        if report.restored:
            lines.append(f"✅ Модель снова отвечает: клиник возвращено в auto — {report.restored}.")
        for patient in report.unanswered:
            logger.error(
                "ai_patient_unanswered",
                kind=patient.kind,
                clinic_id=str(patient.clinic_id),
                conversation_id=str(patient.conversation_id),
                waited_minutes=patient.waited_minutes,
                status=patient.status,
            )
        if report.unanswered:
            shown = report.unanswered[:_UNANSWERED_LINES]
            lines.append("⏳ Пациенты ждут ответа клиники:")
            lines.extend(
                f"• {p.clinic_name} — …{p.phone[-4:]}, {p.waited_minutes} мин, "
                f"{_UNANSWERED_KINDS.get(p.kind, p.kind)}"
                for p in shown
            )
            if len(report.unanswered) > len(shown):
                lines.append(f"… и ещё {len(report.unanswered) - len(shown)}")
        if lines and self._notifier is not None:
            await self._notifier.notify_team("\n".join(lines), event="ai_health")
