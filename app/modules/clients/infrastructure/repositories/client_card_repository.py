"""Репозиторий карточек клиентов для чата команды Skrezo.

Отдельно от :mod:`client_repository`: тому нужны список и карточка админки с
пагинацией и фильтрами, а здесь — один плоский снимок «кто это и как
связаться» на пачку клиник за один запрос (без похода в базу на каждую).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.domain.models import ClientCard
from app.shared.db.enums import DEFAULT_LANG
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.db.models.subscription import Subscription


class ClientCardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def cards(self, clinic_ids: Sequence[uuid.UUID]) -> list[ClientCard]:
        """Карточки клиник в порядке ``clinic_ids``; несуществующие пропускаются.

        Владелец, сотрудники и приёмы подцепляются LEFT JOIN'ами сгруппированных
        подзапросов: клиника без владельца или без единого приёма не должна
        выпадать из результата — о ней команде как раз важнее всего узнать.
        """
        ids = list(dict.fromkeys(clinic_ids))
        if not ids:
            return []

        # Один владелец на клинику: DISTINCT ON страхует от дублей строк, если
        # активных владельцев вдруг окажется два.
        owner = (
            select(
                Membership.clinic_id.label("clinic_id"),
                Account.full_name.label("full_name"),
                Account.phone.label("phone"),
                Membership.specialization_id.label("specialization_id"),
            )
            .join(Role, Role.id == Membership.role_id)
            .join(Account, Account.id == Membership.account_id)
            .where(
                Membership.clinic_id.in_(ids),
                Membership.active.is_(True),
                Role.is_owner.is_(True),
            )
            .distinct(Membership.clinic_id)
            .order_by(Membership.clinic_id, Membership.created_at)
            .subquery()
        )
        staff = (
            select(
                Membership.clinic_id.label("clinic_id"),
                func.count().label("staff_count"),
            )
            .where(Membership.clinic_id.in_(ids), Membership.active.is_(True))
            .group_by(Membership.clinic_id)
            .subquery()
        )
        # Пациенты клиники — те, кого она принимала (пациент в Skrezo глобальный);
        # тот же счёт, что в карточке клиента и в отчётах.
        activity = (
            select(
                Appointment.clinic_id.label("clinic_id"),
                func.count(distinct(Appointment.patient_id)).label("patients_count"),
                func.count(Appointment.id).label("appointments_count"),
            )
            .where(Appointment.clinic_id.in_(ids))
            .group_by(Appointment.clinic_id)
            .subquery()
        )

        rows = (
            await self._session.execute(
                select(
                    Clinic.id,
                    Clinic.name,
                    Clinic.type,
                    CityTranslation.value.label("city_name"),
                    Clinic.address,
                    Clinic.business_phone,
                    owner.c.full_name,
                    owner.c.phone,
                    SpecializationTranslation.value.label("specialization"),
                    Clinic.created_at,
                    Subscription.trial_started_at,
                    Subscription.trial_ends_at,
                    func.coalesce(staff.c.staff_count, 0).label("staff_count"),
                    func.coalesce(activity.c.patients_count, 0).label("patients_count"),
                    func.coalesce(activity.c.appointments_count, 0).label("appointments_count"),
                )
                .select_from(Clinic)
                .outerjoin(
                    CityTranslation,
                    and_(
                        CityTranslation.city_id == Clinic.city_id,
                        CityTranslation.lang == DEFAULT_LANG,
                    ),
                )
                .outerjoin(owner, owner.c.clinic_id == Clinic.id)
                .outerjoin(
                    SpecializationTranslation,
                    and_(
                        SpecializationTranslation.specialization_id == owner.c.specialization_id,
                        SpecializationTranslation.lang == DEFAULT_LANG,
                    ),
                )
                .outerjoin(Subscription, Subscription.clinic_id == Clinic.id)
                .outerjoin(staff, staff.c.clinic_id == Clinic.id)
                .outerjoin(activity, activity.c.clinic_id == Clinic.id)
                .where(Clinic.id.in_(ids))
            )
        ).all()

        by_id = {
            row.id: ClientCard(
                clinic_id=row.id,
                name=row.name,
                type=row.type,
                city_name=row.city_name,
                address=row.address,
                business_phone=row.business_phone,
                owner_full_name=row.full_name,
                owner_phone=row.phone,
                owner_specialization=row.specialization,
                registered_at=row.created_at,
                trial_started_at=row.trial_started_at,
                trial_ends_at=row.trial_ends_at,
                staff_count=int(row.staff_count),
                patients_count=int(row.patients_count),
                appointments_count=int(row.appointments_count),
            )
            for row in rows
        }
        return [by_id[clinic_id] for clinic_id in ids if clinic_id in by_id]
