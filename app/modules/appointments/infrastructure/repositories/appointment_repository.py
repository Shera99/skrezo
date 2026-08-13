"""Репозиторий приёмов и позиций чека (appointment + appointment_item).

Врач = ``membership``. Позиция ссылается на зубы через ``appointment_item_tooth``
(строка ``patient_tooth`` создаётся, если для пациента такого зуба ещё не было).
Итог приёма — сумма позиций: ``цена за единицу × количество``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.appointments.domain.models import (
    AppointmentItemRow,
    AppointmentRow,
    ItemInput,
)
from app.modules.appointments.domain.pricing import apply_discount
from app.shared.db.enums import AppointmentItemStatus, AppointmentStatus, AppointmentType
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_item import AppointmentItem
from app.shared.db.models.appointment_item_tooth import AppointmentItemTooth
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_tooth import PatientTooth
from app.shared.db.models.service import Service
from app.shared.db.models.service_doctor import ServiceDoctor
from app.shared.db.updates import apply_updates

# Колонки приёма, которые разрешено править через PATCH. Здесь нет clinic_id:
# иначе тело запроса уводит приём в чужую клинику.
_UPDATABLE_FIELDS = frozenset(
    {
        "status",
        "appointment_type",
        "scheduled_at",
        "duration_min",
        "comment",
        "cancel_reason",
        "cancel_comment",
        "no_charge",
        "no_charge_comment",
        # Клиент цену и скидку не присылает (их нет в схеме PATCH) — их ставит
        # прикладной слой: отмена обнуляет итог, снятие отмены пересчитывает из
        # позиций, скидка приходит через свою ручку с проверкой потолка роли.
        "price_total_tiyin",
        "discount_percent",
        "discount_tiyin",
        "doctor_membership_id",
        "service_id",
    }
)
# Из них — NOT NULL в БД: NULL сюда необратимо ломает выборки расписания.
_NOT_NULL_FIELDS = frozenset(
    {
        "status",
        "appointment_type",
        "scheduled_at",
        "duration_min",
        "no_charge",
        "price_total_tiyin",
        "discount_percent",
        "discount_tiyin",
    }
)


class AppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def patient_exists(self, patient_id: uuid.UUID) -> bool:
        return (
            await self._session.execute(select(Patient.id).where(Patient.id == patient_id))
        ).scalar_one_or_none() is not None

    async def service_in_clinic(self, clinic_id: uuid.UUID, service_id: uuid.UUID) -> bool:
        return (
            await self._session.execute(
                select(Service.id).where(Service.id == service_id, Service.clinic_id == clinic_id)
            )
        ).scalar_one_or_none() is not None

    async def doctor_can_do_service(
        self, doctor_id: uuid.UUID, service_id: uuid.UUID
    ) -> bool:
        """Выполняет ли врач эту услугу.

        Назначений нет — услугу делают все (так живут соло-кабинет и весь прайс,
        заведённый до появления связки). Назначения есть — врач обязан быть среди
        них; активность самого врача проверяется отдельно (``is_doctor``).
        """
        assigned = (
            (
                await self._session.execute(
                    select(ServiceDoctor.membership_id).where(
                        ServiceDoctor.service_id == service_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return not assigned or doctor_id in assigned

    # Проверка «врач из этой клиники» живёт в AvailabilityService.is_doctor —
    # одно определение врача на приёмы и на расписание.

    async def _get_or_create_patient_tooth(
        self, patient_id: uuid.UUID, tooth_number: int
    ) -> PatientTooth:
        tooth = (
            await self._session.execute(
                select(PatientTooth).where(
                    PatientTooth.patient_id == patient_id,
                    PatientTooth.tooth_number == tooth_number,
                )
            )
        ).scalar_one_or_none()
        if tooth is None:
            from app.shared.db.enums import ToothStatus

            tooth = PatientTooth(
                patient_id=patient_id, tooth_number=tooth_number, status=ToothStatus.HEALTHY
            )
            self._session.add(tooth)
            await self._session.flush()
        return tooth

    async def create(
        self,
        clinic_id: uuid.UUID,
        patient_id: uuid.UUID,
        *,
        doctor_membership_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
        appointment_type: AppointmentType,
        scheduled_at: datetime | None,  # None только у черновика (status=draft)
        duration_min: int,
        comment: str | None,
        items: list[ItemInput],
        service_prices: dict[uuid.UUID, int],
        status: AppointmentStatus = AppointmentStatus.SCHEDULED,
    ) -> Appointment:
        appointment = Appointment(
            clinic_id=clinic_id,
            patient_id=patient_id,
            doctor_membership_id=doctor_membership_id,
            service_id=service_id,
            appointment_type=appointment_type,
            scheduled_at=scheduled_at,
            duration_min=duration_min,
            comment=comment,
            status=status,
        )
        self._session.add(appointment)
        await self._session.flush()
        await self._write_items(appointment, items, service_prices)
        return appointment

    async def replace_items(
        self,
        appointment: Appointment,
        items: list[ItemInput],
        service_prices: dict[uuid.UUID, int],
    ) -> None:
        """Заменить позиции приёма целиком и пересчитать итог.

        Замена, а не точечная правка: чек приёма — это список, и «положить
        текущее состояние» проще и безопаснее для клиента, чем вести дифф из
        добавлений и удалений. Связки с зубами уходят каскадом вместе с позициями.
        """
        await self._session.execute(
            delete(AppointmentItem).where(AppointmentItem.appointment_id == appointment.id)
        )
        await self._write_items(appointment, items, service_prices)

    async def _write_items(
        self,
        appointment: Appointment,
        items: list[ItemInput],
        service_prices: dict[uuid.UUID, int],
    ) -> None:
        """Записать позиции с привязкой к зубам и пересчитать итог приёма.

        Скидка пересчитывается вместе с позициями: процент задан у приёма, и
        после правки чека он обязан относиться к новой сумме, а не к прежней.
        """
        total = 0
        for index, item in enumerate(items):
            price = item.price_tiyin
            if price is None and item.service_id is not None:
                price = service_prices.get(item.service_id, 0)
            price = price or 0
            total += price * item.quantity
            row = AppointmentItem(
                appointment_id=appointment.id,
                service_id=item.service_id,
                doctor_membership_id=appointment.doctor_membership_id,
                status=item.status,
                price_tiyin=price,
                quantity=item.quantity,
                comment=item.comment,
                position=index,
            )
            self._session.add(row)
            await self._session.flush()
            # dict.fromkeys: один зуб в позиции — один раз (ключ связки составной).
            for tooth in dict.fromkeys(item.teeth):
                patient_tooth = await self._get_or_create_patient_tooth(
                    appointment.patient_id, tooth
                )
                self._session.add(
                    AppointmentItemTooth(item_id=row.id, patient_tooth_id=patient_tooth.id)
                )
        discount, payable = apply_discount(total, appointment.discount_percent)
        appointment.discount_tiyin = discount
        appointment.price_total_tiyin = payable
        await self._session.flush()

    async def list_page(
        self,
        clinic_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None,
        status: AppointmentStatus | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AppointmentRow], int]:
        stmt = (
            select(
                Appointment.id,
                Appointment.patient_id,
                func.concat_ws(" ", Patient.last_name, Patient.first_name).label("patient_name"),
                Account.full_name.label("doctor_name"),
                Appointment.appointment_type,
                Appointment.scheduled_at,
                Appointment.duration_min,
                Appointment.status,
                Appointment.price_total_tiyin,
            )
            .join(Patient, Patient.id == Appointment.patient_id)
            .outerjoin(Membership, Membership.id == Appointment.doctor_membership_id)
            .outerjoin(Account, Account.id == Membership.account_id)
            .where(Appointment.clinic_id == clinic_id)
        )
        if patient_id is not None:
            stmt = stmt.where(Appointment.patient_id == patient_id)
        if status is not None:
            stmt = stmt.where(Appointment.status == status)
        if from_dt is not None:
            stmt = stmt.where(Appointment.scheduled_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(Appointment.scheduled_at <= to_dt)

        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                # nullslast: у черновиков даты нет, и в DESC Postgres поднял бы их
                # наверх — реальное расписание важнее, они ищутся фильтром status.
                stmt.order_by(Appointment.scheduled_at.desc().nullslast())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        items = [
            AppointmentRow(
                id=r[0],
                patient_id=r[1],
                patient_name=(r[2] or "").strip(),
                doctor_name=r[3],
                appointment_type=r[4],
                scheduled_at=r[5],
                duration_min=r[6],
                status=r[7],
                price_total_tiyin=r[8],
            )
            for r in rows
        ]
        return items, total

    async def get(self, clinic_id: uuid.UUID, appointment_id: uuid.UUID) -> Appointment | None:
        appt = await self._session.get(Appointment, appointment_id)
        return appt if appt is not None and appt.clinic_id == clinic_id else None

    async def patient_name(self, patient_id: uuid.UUID) -> str:
        row = (
            await self._session.execute(
                select(Patient.last_name, Patient.first_name).where(Patient.id == patient_id)
            )
        ).first()
        if row is None:
            return ""
        return " ".join(part for part in (row[0], row[1]) if part)

    async def doctor_name(self, membership_id: uuid.UUID | None) -> str | None:
        if membership_id is None:
            return None
        return (
            await self._session.execute(
                select(Account.full_name)
                .join(Membership, Membership.account_id == Account.id)
                .where(Membership.id == membership_id)
            )
        ).scalar_one_or_none()

    async def items(self, appointment_id: uuid.UUID) -> Sequence[AppointmentItemRow]:
        """Позиции приёма с номерами зубов (у позиции их может быть 0, 1 или много)."""
        rows = (
            await self._session.execute(
                select(
                    AppointmentItem.id,
                    AppointmentItem.service_id,
                    Service.name,
                    AppointmentItem.status,
                    AppointmentItem.price_tiyin,
                    AppointmentItem.quantity,
                    AppointmentItem.comment,
                    # array_remove: у позиции без зубов outer join даёт {NULL}.
                    func.array_remove(func.array_agg(PatientTooth.tooth_number), None),
                )
                .outerjoin(Service, Service.id == AppointmentItem.service_id)
                .outerjoin(
                    AppointmentItemTooth, AppointmentItemTooth.item_id == AppointmentItem.id
                )
                .outerjoin(PatientTooth, PatientTooth.id == AppointmentItemTooth.patient_tooth_id)
                .where(AppointmentItem.appointment_id == appointment_id)
                .group_by(AppointmentItem.id, Service.name)
                .order_by(AppointmentItem.position, AppointmentItem.id)
            )
        ).all()
        return [
            AppointmentItemRow(
                id=r[0],
                service_id=r[1],
                service_name=r[2],
                status=r[3],
                price_tiyin=r[4],
                quantity=r[5],
                comment=r[6],
                teeth=sorted(r[7] or []),
            )
            for r in rows
        ]

    async def mark_items_done(self, appointment_id: uuid.UUID) -> None:
        """Закрытие приёма доводит незакрытые позиции до ``completed``.

        Закрыли приём — значит работы сделаны; заставлять клиента отдельно
        проставлять статус каждой строки бессмысленно, а без этого статус так и
        остался бы вечным ``planned``. Отменённые позиции не трогаем: врач уже
        сказал, что их не делал, и закрытие приёма это не отменяет.
        """
        await self._session.execute(
            update(AppointmentItem)
            .where(
                AppointmentItem.appointment_id == appointment_id,
                AppointmentItem.status.in_(
                    (AppointmentItemStatus.PLANNED, AppointmentItemStatus.STARTED)
                ),
            )
            .values(status=AppointmentItemStatus.COMPLETED)
        )
        await self._session.flush()

    async def sync_odontogram(self, appointment_id: uuid.UUID) -> int:
        """Обновить состояние зубов пациента по сделанным позициям приёма.

        Источник истины — ``service.tooth_outcome``: «Пломба» → ``filling``,
        «Коронка» → ``crown``, «Удаление» → ``missing``. Услуги без исхода
        (чистка, консультация) карту не трогают, как и отменённые позиции: врач
        уже сказал, что их не делал.

        ``DISTINCT ON`` — не украшение: если в одном приёме зуб сначала
        запломбировали, а потом удалили, побеждает **последняя** позиция чека
        (по ``position``). Без явного порядка Postgres взял бы произвольную
        строку: ``created_at`` у позиций одного приёма одинаков — это время
        транзакции.
        """
        outcome = (
            select(
                AppointmentItemTooth.patient_tooth_id.label("patient_tooth_id"),
                Service.tooth_outcome.label("outcome"),
            )
            .join(AppointmentItem, AppointmentItem.id == AppointmentItemTooth.item_id)
            .join(Service, Service.id == AppointmentItem.service_id)
            .where(
                AppointmentItem.appointment_id == appointment_id,
                AppointmentItem.status == AppointmentItemStatus.COMPLETED,
                Service.tooth_outcome.is_not(None),
            )
            .distinct(AppointmentItemTooth.patient_tooth_id)
            .order_by(
                AppointmentItemTooth.patient_tooth_id,
                AppointmentItem.position.desc(),
                AppointmentItem.id.desc(),
            )
            .subquery()
        )
        updated = (
            await self._session.execute(
                update(PatientTooth)
                .where(PatientTooth.id == outcome.c.patient_tooth_id)
                .values(status=outcome.c.outcome)
                .returning(PatientTooth.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        await self._session.flush()
        return len(list(updated))

    async def has_items(self, appointment_id: uuid.UUID) -> bool:
        """Есть ли у приёма хотя бы одна позиция."""
        return (
            await self._session.execute(
                select(AppointmentItem.id)
                .where(AppointmentItem.appointment_id == appointment_id)
                .limit(1)
            )
        ).scalar_one_or_none() is not None

    async def items_total(self, appointment_id: uuid.UUID) -> int:
        """Итог приёма, пересчитанный из позиций: Σ(цена за единицу × количество)."""
        return int(
            (
                await self._session.execute(
                    select(
                        func.coalesce(
                            func.sum(AppointmentItem.price_tiyin * AppointmentItem.quantity), 0
                        )
                    ).where(AppointmentItem.appointment_id == appointment_id)
                )
            ).scalar_one()
        )

    async def service_prices(
        self, service_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not service_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Service.id, Service.price_tiyin).where(Service.id.in_(service_ids))
            )
        ).all()
        return {r[0]: r[1] for r in rows}

    async def update(self, appointment: Appointment, fields: dict[str, Any]) -> None:
        """Записать разрешённые поля приёма."""
        apply_updates(
            appointment,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="приёма",
        )
        await self._session.flush()
