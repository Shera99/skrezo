"""Диспетчер событий: outbox → адресаты → инбокс → каналы (ТЗ-СУ §6).

Единственное место, где решается «кому и как». Работает в воркере (цикл в
``app/jobs/dispatcher_loop.py``), в тестах зовётся напрямую с их сессией.

Алгоритм на событие:

1. адресаты по каталогу (врач приёма — «свои», менеджеры, владелец) —
   **снимок** в ``inbox_item``, история не переписывается сменой ролей;
2. приоритет: для ``appointment.*`` — по горизонту до приёма (§5), «+1» у
   переноса и отмены; остальным — статический из каталога;
3. текст на языке адресата (``account.lang``), время — в поясе клиники;
4. инбокс (идемпотентно), сигнал в ``RealtimeGateway`` (до Ф4 — заглушка);
5. пуш: ``critical`` — немедленно на устройства адресата (минус отключённые
   типы из префов). ``normal`` через ``push_job`` с отменой по показу —
   следующий инкремент Ф3: до него normal живёт в инбоксе и на экране.

Ошибка обработки события копится в ``attempts``/``last_error``; после пятой
попытки строка остаётся в журнале как алерт и очередь не блокирует.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.domain import catalog
from app.modules.push.domain.events import (
    EVENT_CATALOG,
    Audience,
    EventSpec,
    Rung,
    format_som,
    push_ttl_seconds,
    resolve_rung,
    stored_priority,
)
from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository
from app.modules.push.infrastructure.repositories.event_repository import EventRepository
from app.modules.push.infrastructure.repositories.inbox_repository import InboxRepository
from app.modules.push.infrastructure.repositories.notification_pref_repository import (
    NotificationPrefRepository,
)
from app.modules.push.infrastructure.repositories.push_job_repository import (
    PushJobRepository,
)
from app.shared.db.enums import Lang
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.event import Event
from app.shared.db.models.inbox_item import InboxItem
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.role import Role
from app.shared.push.channels import DeadTokenError, PushDeliveryError
from app.shared.push.message import PushMessage
from app.shared.push.transport import PushTransport
from app.shared.realtime import RealtimeGateway

logger = structlog.get_logger(__name__)

#: Фора normal-пуша: если за это время приложение подтвердило ПОКАЗ записи
#: (человек уже смотрит в экран), пуш не нужен и job отменяется (ТЗ-СУ §3).
NORMAL_PUSH_DELAY = timedelta(seconds=10)


@dataclass(frozen=True, slots=True)
class _Recipient:
    account_id: uuid.UUID
    lang: Lang


@dataclass(frozen=True, slots=True)
class _Context:
    """Что диспетчер дочитал из БД к моменту доставки (не из payload!)."""

    scheduled_at: datetime | None
    doctor_membership_id: uuid.UUID | None
    params: dict[str, str]  # плейсхолдеры текста: patientName, time, amount
    tz: ZoneInfo  # пояс клиники — время в текстах и группировка «по дню»


class EventDispatcher:
    """Разобрать пачку необработанных событий outbox."""

    def __init__(
        self,
        session: AsyncSession,
        transport: PushTransport,
        realtime: RealtimeGateway,
    ) -> None:
        self._session = session
        self._events = EventRepository(session)
        self._inbox = InboxRepository(session)
        self._devices = DeviceRepository(session)
        self._prefs = NotificationPrefRepository(session)
        self._jobs = PushJobRepository(session)
        self._transport = transport
        self._realtime = realtime

    async def dispatch_pending(self, limit: int = 50) -> int:
        """Обработать до ``limit`` событий; вернуть число обработанных.

        Коммит на каждое событие: упавшее не откатывает доставленные до него,
        а его собственные полудоставки стирает rollback.
        """
        processed = 0
        for event in await self._events.pending_batch(limit):
            try:
                await self._dispatch(event)
                self._events.mark_processed(event)
                await self._session.commit()
                processed += 1
            except Exception as exc:  # очередь важнее одного события
                await self._session.rollback()
                self._events.mark_failed(event, str(exc))
                await self._session.commit()
                logger.error(
                    "event_dispatch_failed",
                    event_id=event.id,
                    type=event.type,
                    attempts=event.attempts,
                    error=str(exc),
                )
        return processed

    # ── Одно событие ────────────────────────────────────────────────────────

    async def _dispatch(self, event: Event) -> None:
        spec = EVENT_CATALOG.get(event.type)
        if spec is None:
            # Незнакомый тип (например, ai.* до Ф2-каталога) — не ошибка:
            # событие остаётся в журнале обработанным, доставки нет.
            logger.warning("event_type_unknown", event_id=event.id, type=event.type)
            return
        context = await self._load_context(event)
        if context is None:
            logger.warning("event_entity_missing", event_id=event.id, type=event.type)
            return

        now = datetime.now(UTC)
        rung = resolve_rung(spec, context.scheduled_at, now)
        priority = stored_priority(rung)
        recipients = await self._resolve_recipients(event, spec, context)
        if not recipients:
            return

        rows = []
        for recipient in recipients:
            texts = catalog.render(event.type, recipient.lang, **context.params) or {}
            rows.append(
                {
                    "event_id": event.id,
                    "clinic_id": event.clinic_id,
                    "account_id": recipient.account_id,
                    "type": event.type,
                    "priority": priority,
                    "title": texts.get("title") or event.type,
                    "body": texts.get("body") or "",
                    "lang": recipient.lang,
                    "entity_type": event.entity_type,
                    "entity_id": event.entity_id,
                }
            )
        inserted_ids = await self._inbox.insert_items(rows)

        # Сигнал открытым экранам (Ф4). Идёт до пушей: он дешевле и мгновенный.
        await self._realtime.emit(
            event.clinic_id,
            {"type": event.type, "entityType": event.entity_type, "entityId": str(event.entity_id)},
        )

        if rung is Rung.CRITICAL:
            await self._push_now(event, spec, recipients, context)
        elif rung is Rung.NORMAL:
            # Пуш с форой: отправит send_due_jobs(), если показ не подтвердят.
            # Job'ы — только на ВСТАВЛЕННЫЕ строки: повторная обработка события
            # не дублирует ни инбокс, ни отложенные пуши.
            await self._jobs.schedule(
                inserted_ids, send_after=datetime.now(UTC) + NORMAL_PUSH_DELAY
            )

    # ── Контекст события ────────────────────────────────────────────────────

    async def _load_context(self, event: Event) -> _Context | None:
        """Дочитать сущность из БД: тексты и адресаты не должны протухать.

        Payload события — снимок для отладки; правда — текущее состояние. Так
        перенос, случившийся между эмиссией и доставкой, уйдёт с новым
        временем, а не с тем, что было на момент записи события.
        """
        tz = await self._clinic_tz(event.clinic_id)
        if event.type.startswith("appointment."):
            row = (
                await self._session.execute(
                    select(
                        Appointment.scheduled_at,
                        Appointment.doctor_membership_id,
                        func.concat_ws(" ", Patient.last_name, Patient.first_name),
                    )
                    .join(Patient, Patient.id == Appointment.patient_id)
                    .where(Appointment.id == event.entity_id)
                )
            ).first()
            if row is None:
                return None
            scheduled_at, doctor_id, patient_name = row[0], row[1], (row[2] or "").strip()
            return _Context(
                scheduled_at=scheduled_at,
                doctor_membership_id=doctor_id,
                params={
                    "patientName": patient_name or "пациент",
                    "time": self._format_time(scheduled_at, tz),
                },
                tz=tz,
            )
        if event.type == "payment.created":
            patient_name = ""
            patient_id = event.payload.get("patientId")
            if patient_id:
                patient_name = (
                    (
                        await self._session.execute(
                            select(
                                func.concat_ws(" ", Patient.last_name, Patient.first_name)
                            ).where(Patient.id == uuid.UUID(str(patient_id)))
                        )
                    ).scalar_one_or_none()
                    or ""
                ).strip()
            amount = int(event.payload.get("amountTiyin") or 0)
            return _Context(
                scheduled_at=None,
                doctor_membership_id=None,
                params={
                    "patientName": patient_name or "пациент",
                    "amount": format_som(amount),
                },
                tz=tz,
            )
        return _Context(scheduled_at=None, doctor_membership_id=None, params={}, tz=tz)

    async def _clinic_tz(self, clinic_id: uuid.UUID) -> ZoneInfo:
        name = (
            await self._session.execute(select(Clinic.timezone).where(Clinic.id == clinic_id))
        ).scalar_one_or_none()
        try:
            return ZoneInfo(name or "Asia/Bishkek")
        except (KeyError, ValueError):
            return ZoneInfo("Asia/Bishkek")

    @staticmethod
    def _format_time(scheduled_at: datetime | None, tz: ZoneInfo) -> str:
        """Время приёма в поясе КЛИНИКИ — врач в отпуске не должен гадать."""
        if scheduled_at is None:
            return ""
        return scheduled_at.astimezone(tz).strftime("%d.%m %H:%M")

    # ── Адресаты ────────────────────────────────────────────────────────────

    async def _resolve_recipients(
        self, event: Event, spec: EventSpec, context: _Context
    ) -> list[_Recipient]:
        """Раскрыть роли каталога в аккаунты клиники (снимок «сейчас»).

        Врач — только своего приёма («свои» из §4): фильтр применяется здесь,
        а не на клиенте. Менеджер/владелец — по системной роли живого членства.
        """
        recipients: dict[uuid.UUID, _Recipient] = {}

        need_roles = spec.audience & {Audience.MANAGER, Audience.OWNER}
        if need_roles:
            conditions: list[ColumnElement[bool]] = []
            if Audience.OWNER in need_roles:
                conditions.append(Role.is_owner.is_(True))
            if Audience.MANAGER in need_roles:
                conditions.append(Role.key == "manager")
            rows = await self._session.execute(
                select(Membership.account_id, Account.lang)
                .join(Role, Role.id == Membership.role_id)
                .join(Account, Account.id == Membership.account_id)
                .where(
                    Membership.clinic_id == event.clinic_id,
                    Membership.active.is_(True),
                    or_(*conditions),
                )
            )
            for account_id, lang in rows:
                recipients[account_id] = _Recipient(account_id=account_id, lang=lang)

        if Audience.DOCTOR in spec.audience and context.doctor_membership_id is not None:
            row = (
                await self._session.execute(
                    select(Membership.account_id, Account.lang)
                    .join(Account, Account.id == Membership.account_id)
                    .where(
                        Membership.id == context.doctor_membership_id,
                        Membership.active.is_(True),
                    )
                )
            ).first()
            if row is not None:
                recipients[row[0]] = _Recipient(account_id=row[0], lang=row[1])
        return list(recipients.values())

    # ── Отложенные пуши (normal) ────────────────────────────────────────────

    async def send_due_jobs(self, limit: int = 50) -> int:
        """Отправить созревшие push_job'ы; вернуть число обработанных.

        Захват атомарный (``claim_due`` сразу ставит ``sent_at``), поэтому
        упавшая доставка НЕ возвращает job в очередь: пуш — деградация, инбокс
        уже доставлен, а повторный пуш хуже пропущенного дубля.
        """
        claimed = await self._jobs.claim_due(limit)
        for _job, item in claimed:
            try:
                await self._send_job_push(item)
            except Exception as exc:  # одна ошибка не валит пачку
                logger.error("push_job_send_failed", inbox_item_id=item.id, error=str(exc))
        if claimed:
            await self._session.commit()
        return len(claimed)

    async def next_job_due_at(self) -> datetime | None:
        """Ближайший срок отправки — цикл воркера подбирает под него сон."""
        return await self._jobs.next_due_at()

    async def _send_job_push(self, item: InboxItem) -> None:
        """Пуш по снимку инбокса: текст уже зафиксирован на языке адресата.

        Пер-устройственного рендера здесь нет намеренно: показанное в пуше
        обязано совпадать с записью в инбоксе, на которую он указывает.
        """
        pref = await self._prefs.get(item.account_id, item.clinic_id)
        if pref is not None and item.type in (pref.disabled or []):
            return
        devices = await self._devices.list_by_accounts(item.clinic_id, [item.account_id])
        if not devices:
            return

        spec = EVENT_CATALOG.get(item.type)
        scheduled_at: datetime | None = None
        tz = await self._clinic_tz(item.clinic_id)
        if item.entity_type == "appointment" and item.entity_id is not None:
            scheduled_at = (
                await self._session.execute(
                    select(Appointment.scheduled_at).where(Appointment.id == item.entity_id)
                )
            ).scalar_one_or_none()
        now = datetime.now(UTC)
        ttl = push_ttl_seconds(spec, scheduled_at, now) if spec else 4 * 3600
        thread_id = None
        if item.entity_type == "appointment" and scheduled_at is not None:
            thread_id = f"day:{scheduled_at.astimezone(tz).date().isoformat()}"
        elif item.type.startswith("payment."):
            thread_id = "cash"
        badge = await self._inbox.unread_count(item.account_id, item.clinic_id)

        data: dict[str, str] = {
            # Тот же id, что у critical-пути: клиент дедуплицирует по событию.
            "type": item.type,
            "id": f"evt_{item.event_id}",
            "clinicId": str(item.clinic_id),
        }
        if item.entity_type and item.entity_id:
            data["entityType"] = item.entity_type
            data["entityId"] = str(item.entity_id)
        message = PushMessage(
            data=data,
            title=item.title or None,
            body=item.body or None,
            collapse_key=f"{item.entity_type}:{item.entity_id}",
            urgent=False,
            android_channel=spec.android_channel if spec else "schedule-changes.v1",
            ttl_seconds=ttl,
            badge=badge,
            thread_id=thread_id,
        )
        dead: list[str] = []
        for device in devices:
            try:
                await self._transport.send(device.platform, device.token, message)
            except DeadTokenError:
                dead.append(device.token)
            except PushDeliveryError as exc:
                logger.warning("push_delivery_failed", type=item.type, error=str(exc))
        for token in dead:
            await self._devices.delete_dead(token)

    # ── Пуш critical ────────────────────────────────────────────────────────

    def _thread_id(self, event: Event, context: _Context) -> str | None:
        """Группировка (§10): приёмы стопкой по дню клиники, касса — одной."""
        if event.type.startswith("appointment.") and context.scheduled_at is not None:
            local_day = context.scheduled_at.astimezone(context.tz).date().isoformat()
            return f"day:{local_day}"
        if event.type.startswith("payment."):
            return "cash"
        return None

    async def _push_now(
        self, event: Event, spec: EventSpec, recipients: list[_Recipient], context: _Context
    ) -> None:
        """Немедленный пуш адресатам (минус отключённые типы из префов)."""
        allowed: list[uuid.UUID] = []
        for recipient in recipients:
            pref = await self._prefs.get(recipient.account_id, event.clinic_id)
            if pref is not None and event.type in (pref.disabled or []):
                continue
            allowed.append(recipient.account_id)
        devices = await self._devices.list_by_accounts(event.clinic_id, allowed)
        if not devices:
            return

        collapse_key = f"{event.entity_type}:{event.entity_id}"
        ttl = push_ttl_seconds(spec, context.scheduled_at, datetime.now(UTC))
        thread_id = self._thread_id(event, context)
        # Бейдж — в каждый пуш: счётчик на устройстве самовосстанавливается
        # после любых пропусков. Считается ПОСЛЕ вставки инбокса — свежий.
        badges: dict[uuid.UUID, int] = {
            account_id: await self._inbox.unread_count(account_id, event.clinic_id)
            for account_id in allowed
        }
        dead: list[str] = []
        for device in devices:
            texts = catalog.render(event.type, device.locale, **context.params) or {}
            data: dict[str, str] = {
                "type": event.type,
                "id": f"evt_{event.id}",
                "clinicId": str(event.clinic_id),
            }
            if event.entity_type and event.entity_id:
                data["entityType"] = event.entity_type
                data["entityId"] = str(event.entity_id)
            if texts.get("speak"):
                data["speak"] = texts["speak"]
            message = PushMessage(
                data=data,
                title=texts.get("title") or None,
                body=texts.get("body") or None,
                collapse_key=collapse_key,
                urgent=True,
                android_channel=spec.android_channel,
                ttl_seconds=ttl,
                badge=badges.get(device.account_id),
                thread_id=thread_id,
            )
            try:
                await self._transport.send(device.platform, device.token, message)
            except DeadTokenError:
                dead.append(device.token)
            except PushDeliveryError as exc:
                # Один недоступный провайдер не должен валить событие в ретраи
                # целиком: инбокс уже доставлен, пуш — деградация.
                logger.warning("push_delivery_failed", type=event.type, error=str(exc))
        for token in dead:
            await self._devices.delete_dead(token)
