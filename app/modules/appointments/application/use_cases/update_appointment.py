"""Use-case: обновить приём (статус/комментарий/врач/дата/услуга/длительность)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.modules.appointments.application.services.booking import booking_guard
from app.modules.appointments.application.services.closing import (
    ensure_closable,
    ensure_no_charge_valid,
)
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.domain.pricing import apply_discount
from app.modules.appointments.domain.transitions import TransitionKind, classify_transition
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.push.application.outbox import emit_event
from app.modules.schedule.application.services.availability_service import (
    AvailabilityService,
    span_of,
)
from app.shared.db.enums import AppointmentStatus, CancelReason
from app.shared.db.models.appointment import Appointment

# Поля, при смене которых приём нужно перепроверить на окно и пересечения.
_BOOKING_FIELDS = frozenset({"scheduled_at", "duration_min", "doctor_membership_id", "service_id"})


def _affects_booking(appointment: Appointment, fields: dict[str, Any]) -> bool:
    """Нужно ли перепроверять занятость слота.

    Кроме смены геометрии приёма (время/длительность/врач/услуга) проверку обязано
    проходить и **снятие отмены**: пока приём был отменён, слот был свободен и мог
    достаться другому. Без этого случая единственной защитой остаётся констрейнт,
    а он отдаёт 500 вместо человекочитаемого 409.

    То же и с **доведением черновика** до записи: слот он не занимал, а после
    превращения в обычный приём обязан пройти все проверки.
    """
    if _BOOKING_FIELDS & fields.keys():
        return True
    new_status = fields.get("status", appointment.status)
    was_free = appointment.status in (AppointmentStatus.CANCELLED, AppointmentStatus.DRAFT)
    return was_free and new_status != appointment.status


def _event_type_for(appointment: Appointment, fields: dict[str, Any]) -> str | None:
    """Каким событием каталога является эта правка (до применения fields).

    Отмена и неявка важнее переноса: если PATCH одновременно двигает время и
    отменяет, врачу нужно «приём отменён», а не «перенесён». Черновик,
    доведённый до записи, для расписания — новая запись: appointment.created.
    Правка комментария или чека событием не является.
    """
    old_status = appointment.status
    target = fields.get("status", old_status)
    if target is not old_status and target in (
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    ):
        return "appointment.canceled"
    if old_status is AppointmentStatus.DRAFT and target is AppointmentStatus.SCHEDULED:
        return "appointment.created"
    moved = ("scheduled_at" in fields and fields["scheduled_at"] != appointment.scheduled_at) or (
        "doctor_membership_id" in fields
        and fields["doctor_membership_id"] != appointment.doctor_membership_id
    )
    if moved:
        return "appointment.moved"
    if target is not old_status:
        return "appointment.status_changed"
    return None


def _apply_cancel_reason(appointment: Appointment, fields: dict[str, Any]) -> None:
    """Причина отмены обязательна и живёт только у отменённого приёма.

    Справочник вместо свободного текста — иначе отмены нечем считать. При
    ``other`` обязателен комментарий, иначе «иное» не несёт информации. Снятие
    отмены очищает оба поля, чтобы у рабочего приёма не висел прошлый повод.
    """
    target = fields.get("status", appointment.status)
    if target is AppointmentStatus.CANCELLED:
        reason = fields.get("cancel_reason", appointment.cancel_reason)
        if reason is None:
            raise ValidationAppError(
                "Укажите причину отмены (cancelReason)", code="cancel_reason_required"
            )
        comment = fields.get("cancel_comment", appointment.cancel_comment)
        if reason is CancelReason.OTHER and not comment:
            raise ValidationAppError(
                "При причине «иное» нужен комментарий (cancelComment)",
                code="cancel_comment_required",
            )
        return

    if fields.get("cancel_reason") is not None or fields.get("cancel_comment") is not None:
        raise ValidationAppError(
            "Причину отмены можно указать только вместе со статусом cancelled",
            code="cancel_reason_without_cancel",
        )
    # Отмену сняли — повод больше не относится к приёму.
    if appointment.cancel_reason is not None:
        fields["cancel_reason"] = None
        fields["cancel_comment"] = None


def _ensure_date_for_status(appointment: Appointment, fields: dict[str, Any]) -> None:
    """Дата обязательна везде, кроме приёмов, которые так и не состоялись.

    Исключения — черновик и отмена: передумавшего пациента закрывают отменой,
    и даты у него не появилось. Так «доведение» черновика до записи без
    ``scheduledAt`` отвергается понятной ошибкой, а не нарушением CHECK в БД.
    """
    new_status = fields.get("status", appointment.status)
    if new_status in (AppointmentStatus.DRAFT, AppointmentStatus.CANCELLED):
        return
    if fields.get("scheduled_at", appointment.scheduled_at) is None:
        raise ValidationAppError(
            "Укажите дату приёма: без неё запись возможна только как черновик",
            code="scheduled_at_required",
        )


def _ensure_doctor_for_status(appointment: Appointment, fields: dict[str, Any]) -> None:
    """Врач обязателен везде, кроме приёмов, которые так и не состоялись.

    Исключения те же, что у даты: черновик (ещё ничего не выбрано) и отмена
    (передумавшего пациента закрывают, и врач у него так и не появился). Иначе
    «доведение» черновика до записи прошло бы без врача, и приём потерялся бы —
    в ленте дня он не попадает ни в одну колонку.
    """
    new_status = fields.get("status", appointment.status)
    if new_status in (AppointmentStatus.DRAFT, AppointmentStatus.CANCELLED):
        return
    if fields.get("doctor_membership_id", appointment.doctor_membership_id) is None:
        raise ValidationAppError(
            "Укажите врача: без него запись возможна только как черновик",
            code="doctor_required",
        )


class UpdateAppointmentUseCase:
    def __init__(
        self,
        session: AsyncSession,
        appointments: AppointmentRepository,
        availability: AvailabilityService,
        audit: AuditService,
    ) -> None:
        self._session = session
        self._appointments = appointments
        self._availability = availability
        self._audit = audit

    async def execute(
        self,
        clinic_id: uuid.UUID,
        appointment_id: uuid.UUID,
        fields: dict[str, Any],
        *,
        actor_id: uuid.UUID | None,
        can_rollback: bool = False,
        rollback_comment: str | None = None,
        can_force: bool = False,
        force: bool = False,
        via: str | None = None,
    ) -> AppointmentDetail:
        """``actor_id=None`` + ``via="assistant"`` — правка по кнопке пациента (Ф7)."""
        if force and not can_force:
            raise ForbiddenError(
                "Нужно право на запись вне графика врача", code="force_booking_forbidden"
            )
        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")

        fields = dict(fields)
        await self._validate_refs(clinic_id, fields)
        await self._resolve_duration(clinic_id, fields)
        rollback_from = await self._check_transition(
            clinic_id, appointment, fields, can_rollback=can_rollback, comment=rollback_comment
        )
        _apply_cancel_reason(appointment, fields)
        _ensure_date_for_status(appointment, fields)
        _ensure_doctor_for_status(appointment, fields)
        await self._ensure_doctor_can_do_service(appointment, fields, force=force)
        await self._apply_no_charge(appointment, fields)
        await self._apply_price(appointment, fields)
        await self._ensure_bookable(clinic_id, appointment, fields, force=force)

        # Закрытие через PATCH — тот же финансовый факт, что и ручка завершения,
        # поэтому позиции доводятся одинаково (иначе статус зависел бы от пути).
        closing = (
            fields.get("status") is AppointmentStatus.COMPLETED
            and appointment.status is not AppointmentStatus.COMPLETED
        )

        event_type = _event_type_for(appointment, fields)

        # Гвард накрывает и update() (внутри флашит UPDATE — там и срабатывает
        # констрейнт), и commit(): ловить только коммит недостаточно.
        async with booking_guard(self._session):
            await self._appointments.update(appointment, fields)
            if closing:
                await self._appointments.mark_items_done(appointment_id)
                await self._appointments.sync_odontogram(appointment_id)
            # Событие — в этой же транзакции (outbox); тип определён ДО update,
            # пока старые значения ещё были видны.
            if event_type is not None:
                await emit_event(
                    self._session,
                    clinic_id=clinic_id,
                    type_=event_type,
                    entity_type="appointment",
                    entity_id=appointment_id,
                )
            await self._session.commit()

        # Одна запись на операцию, но разная по подробности: откат — подотчётное
        # действие со своей причиной, обычная правка — перечень изменённых полей.
        # Писать обе значило бы дважды рассказывать про одно и то же.
        if rollback_from is not None:
            await self._audit.record_clinic(
                actor_id=actor_id,
                clinic_id=clinic_id,
                action="appointment.status_rollback",
                entity_type="appointment",
                entity_id=appointment_id,
                payload={
                    "from": rollback_from.value,
                    "to": appointment.status.value,
                    "comment": rollback_comment,
                },
            )
        else:
            await self._audit.record_clinic(
                actor_id=actor_id,
                clinic_id=clinic_id,
                action="appointment.update",
                entity_type="appointment",
                entity_id=appointment_id,
                payload={"fields": sorted(fields), **({"via": via} if via else {})},
            )

        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        items = list(await self._appointments.items(appointment_id))
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            items=items,
        )

    async def _check_transition(
        self,
        clinic_id: uuid.UUID,
        appointment: Appointment,
        fields: dict[str, Any],
        *,
        can_rollback: bool,
        comment: str | None,
    ) -> AppointmentStatus | None:
        """Проверить смену статуса. Вернуть прежний статус, если это был откат.

        Откат разрешён узко: только предусмотренные пары, только с правом
        ``appointments.status_rollback``, только для приёма текущего дня и всегда
        с комментарием — иначе исправление ошибок превращается в тихое
        переписывание истории.
        """
        target = fields.get("status")
        if target is None:
            return None

        kind = classify_transition(appointment.status, target)
        if kind is TransitionKind.FORBIDDEN:
            raise ConflictError(
                f"Недопустимый переход статуса: {appointment.status.value} → {target.value}",
                code="invalid_status_transition",
            )
        if kind is not TransitionKind.ROLLBACK:
            return None

        if not can_rollback:
            raise ForbiddenError(
                "Нужно право на откат статуса приёма", code="status_rollback_forbidden"
            )
        if not comment:
            raise ValidationAppError(
                "Откат статуса требует комментария (rollbackComment)",
                code="rollback_comment_required",
            )
        await self._ensure_today(clinic_id, appointment, fields)
        return appointment.status

    async def _ensure_today(
        self, clinic_id: uuid.UUID, appointment: Appointment, fields: dict[str, Any]
    ) -> None:
        """Откатывать можно только приём текущего дня (в таймзоне клиники).

        Иначе появляется способ править статистику прошедших дней задним числом.
        """
        scheduled_at = fields.get("scheduled_at", appointment.scheduled_at)
        tz = await self._availability.clinic_timezone(clinic_id)
        today = datetime.now(tz).date()
        if scheduled_at is None or scheduled_at.astimezone(tz).date() != today:
            raise ValidationAppError(
                "Откат статуса возможен только для приёма текущего дня",
                code="rollback_not_today",
            )

    async def _apply_no_charge(self, appointment: Appointment, fields: dict[str, Any]) -> None:
        """Флаг «бесплатно» и закрытие приёма через PATCH.

        Закрыть приём можно и правкой (``status: completed``), поэтому правило
        «без позиций закрывать нельзя» проверяется и здесь — иначе PATCH стал бы
        дырой в обход ручки завершения. Пояснение живёт только вместе с флагом:
        как и причина отмены, при снятии оно очищается.
        """
        no_charge: bool = fields.get("no_charge", appointment.no_charge)
        comment: str | None = fields.get("no_charge_comment", appointment.no_charge_comment)
        if not no_charge:
            if fields.get("no_charge_comment") is not None:
                raise ValidationAppError(
                    "Пояснение можно указать только вместе с noCharge: true",
                    code="no_charge_comment_without_flag",
                )
            if "no_charge" in fields and appointment.no_charge_comment is not None:
                fields["no_charge_comment"] = None
                comment = None

        closing = (
            fields.get("status") is AppointmentStatus.COMPLETED
            and appointment.status is not AppointmentStatus.COMPLETED
        )
        if not closing and "no_charge" not in fields and "no_charge_comment" not in fields:
            return

        has_items = await self._appointments.has_items(appointment.id)
        if closing:
            ensure_closable(has_items=has_items, no_charge=no_charge, comment=comment)
        else:
            ensure_no_charge_valid(has_items=has_items, no_charge=no_charge, comment=comment)

    async def _apply_price(self, appointment: Appointment, fields: dict[str, Any]) -> None:
        """Отмена обнуляет итог приёма; снятие отмены — пересчитывает из позиций.

        Позиции при отмене сохраняются: что успели сделать — часть истории зуба.
        Но денег за несостоявшийся приём не берут, иначе такие случаи закрывают
        как ``completed`` с нулём — то есть врут отчёту. Снятая отмена
        возвращает итог из фактических позиций (и из процента скидки), а не из
        запомненного числа. Сам процент скидки при отмене сохраняется — это
        решение по приёму, а не сумма.
        """
        target = fields.get("status")
        if target is None or target is appointment.status:
            return
        if target is AppointmentStatus.CANCELLED:
            fields["price_total_tiyin"] = 0
            fields["discount_tiyin"] = 0
        elif appointment.status is AppointmentStatus.CANCELLED:
            subtotal = await self._appointments.items_total(appointment.id)
            discount, payable = apply_discount(subtotal, appointment.discount_percent)
            fields["discount_tiyin"] = discount
            fields["price_total_tiyin"] = payable

    async def _validate_refs(self, clinic_id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Врач и услуга обязаны быть из этой клиники — иначе IDOR."""
        doctor_id = fields.get("doctor_membership_id")
        if doctor_id is not None and not await self._availability.is_doctor(clinic_id, doctor_id):
            raise ValidationAppError("Врач не из этой клиники", code="invalid_doctor")
        service_id = fields.get("service_id")
        if service_id is not None and not await self._appointments.service_in_clinic(
            clinic_id, service_id
        ):
            raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")

    async def _resolve_duration(self, clinic_id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Сменили услугу без явной длительности — берём длительность новой услуги.

        Снятие услуги (``serviceId: null``) длительность НЕ трогает: приём на 90
        минут не должен молча схлопываться до дефолтных 30 из-за того, что у него
        отвязали услугу. Нужна другая длительность — передайте ``durationMin``.
        """
        if fields.get("duration_min") is not None:
            return
        fields.pop("duration_min", None)
        if fields.get("service_id") is None:
            return
        fields["duration_min"] = await self._availability.resolve_duration(
            clinic_id, fields["service_id"], None
        )

    async def _ensure_doctor_can_do_service(
        self, appointment: Appointment, fields: dict[str, Any], *, force: bool = False
    ) -> None:
        """Врач против услуги — на любой смене врача ИЛИ услуги.

        Проверять только изменённое поле мало: сменили врача — новый может не
        уметь прежнюю услугу, сменили услугу — прежний врач может не уметь новую.
        Обход — тем же ``force``, что и запись вне графика.
        """
        if force:
            return
        doctor_id = fields.get("doctor_membership_id", appointment.doctor_membership_id)
        service_id = fields.get("service_id", appointment.service_id)
        if doctor_id is None or service_id is None:
            return
        if "doctor_membership_id" not in fields and "service_id" not in fields:
            return
        if not await self._appointments.doctor_can_do_service(doctor_id, service_id):
            raise ValidationAppError(
                "Этот врач не выполняет выбранную услугу", code="doctor_cant_do_service"
            )

    async def _ensure_bookable(
        self,
        clinic_id: uuid.UUID,
        appointment: Appointment,
        fields: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        """Перепроверить слот, если поменялось что-то влияющее на занятость."""
        if not _affects_booking(appointment, fields):
            return
        status = fields.get("status", appointment.status)
        doctor_id = fields.get("doctor_membership_id", appointment.doctor_membership_id)
        # Приём без врача или отменённый слот не занимает — как в EXCLUDE-констрейнте.
        if doctor_id is None or status == AppointmentStatus.CANCELLED:
            return
        scheduled_at = fields.get("scheduled_at", appointment.scheduled_at)
        duration_min = fields.get("duration_min", appointment.duration_min)
        if not isinstance(scheduled_at, datetime):
            return
        await self._availability.ensure_bookable(
            clinic_id,
            doctor_id,
            span_of(scheduled_at, duration_min),
            exclude_appointment_id=appointment.id,
            force=force,
        )
