"""Репозиторий раздела «Клиенты» (админка): список, карточка, правка.

Клиент = клиника (соло или клиника) с владельцем, подпиской и сотрудниками.
Владелец берётся подзапросом (роль ``is_owner``), число мест = активные
не-владельческие членства. Список — offset-пагинация (`page`, `pageSize=25`)
с общим `totalCount`. Названия городов/специализаций резолвятся на язык запроса
с фолбэком на русский.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.clients.domain.models import ClientDetail, ClientListRow, StaffRow
from app.shared.db.enums import DEFAULT_LANG, ClinicType, Lang, PaymentKind, SubscriptionStatus
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_custom_plan import ClinicCustomPlan
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.membership import Membership
from app.shared.db.models.plan_request import PlanRequest
from app.shared.db.models.role import Role
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent
from app.shared.db.updates import apply_updates

# Колонки клиники, которые разрешено править из карточки клиента. Здесь нет
# type/timezone/seats_limit: тип и число мест меняются вместе с подпиской, а не
# правкой карточки.
_UPDATABLE_FIELDS = frozenset(
    {"name", "address", "city_id", "business_phone", "lang", "support_notes"}
)
# Из них — NOT NULL в БД.
_NOT_NULL_FIELDS = frozenset({"name", "lang"})

# Коарс-название тарифа (для фильтра `tariffs[]`) — по типу аккаунта.
_TARIFF_NAME_BY_TYPE = {ClinicType.SOLO: "Соло", ClinicType.CLINIC: "Клиника"}
_TYPE_BY_TARIFF_NAME = {v: k for k, v in _TARIFF_NAME_BY_TYPE.items()}


def tariff_name_for(clinic_type: ClinicType) -> str:
    """Коарс-ярлык тарифа по типу клиники («Соло» | «Клиника»)."""
    return _TARIFF_NAME_BY_TYPE[clinic_type]


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _seats_used_expr() -> Any:
        # Активные членства, кроме владельца (владелец — бесплатно).
        return (
            select(func.count())
            .select_from(Membership)
            .join(Role, Role.id == Membership.role_id)
            .where(
                Membership.clinic_id == Clinic.id,
                Membership.active.is_(True),
                Role.is_owner.is_(False),
            )
            .scalar_subquery()
        )

    @staticmethod
    def _patients_subquery() -> Any:
        """Пациентов на клинику: тот же счёт, что в отчётах — по приёмам.

        Пациент в Skrezo глобальный, «своих» у клиники нет: её пациенты — те,
        кого она принимала. Иначе в карточке стояло бы число пациентов всей
        платформы.
        """
        return (
            select(
                Appointment.clinic_id.label("clinic_id"),
                func.count(distinct(Appointment.patient_id)).label("patients_count"),
            )
            .group_by(Appointment.clinic_id)
            .subquery()
        )

    @staticmethod
    def _revenue_subquery() -> Any:
        """Нетто кассы на клинику за всё время: платежи минус возвраты."""
        signed = func.sum(
            case(
                (CashPayment.kind == PaymentKind.REFUND, -CashPayment.amount_tiyin),
                else_=CashPayment.amount_tiyin,
            )
        )
        return (
            select(
                CashPayment.clinic_id.label("clinic_id"),
                func.coalesce(signed, 0).label("revenue_tiyin"),
            )
            .group_by(CashPayment.clinic_id)
            .subquery()
        )

    def _list_base(self, lang: Lang) -> Select[Any]:
        owner = (
            select(
                Membership.clinic_id.label("clinic_id"),
                Membership.account_id.label("owner_account_id"),
            )
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.active.is_(True), Role.is_owner.is_(True))
            .subquery()
        )
        patients = self._patients_subquery()
        revenue = self._revenue_subquery()
        # Название города — перевод на язык запроса, фолбэк на ru.
        ct_lang = aliased(CityTranslation)
        ct_ru = aliased(CityTranslation)
        return (
            select(
                Clinic.id.label("id"),
                Clinic.type.label("type"),
                Clinic.name.label("name"),
                Clinic.city_id.label("city_id"),
                func.coalesce(ct_lang.value, ct_ru.value).label("city_name"),
                Clinic.seats_limit.label("seats_limit"),
                Clinic.created_at.label("created_at"),
                Account.full_name.label("admin_full_name"),
                Account.phone.label("admin_phone"),
                Account.blocked_at.label("admin_blocked_at"),
                Subscription.status.label("subscription_status"),
                Subscription.trial_started_at.label("trial_started_at"),
                Subscription.current_period_end.label("current_period_end"),
                Subscription.card_attached_at.label("card_attached_at"),
                self._seats_used_expr().label("seats_used"),
                func.coalesce(patients.c.patients_count, 0).label("patients_count"),
                func.coalesce(revenue.c.revenue_tiyin, 0).label("revenue_tiyin"),
            )
            .select_from(Clinic)
            .outerjoin(owner, owner.c.clinic_id == Clinic.id)
            .outerjoin(Account, Account.id == owner.c.owner_account_id)
            .outerjoin(Subscription, Subscription.clinic_id == Clinic.id)
            .outerjoin(ct_lang, and_(ct_lang.city_id == Clinic.city_id, ct_lang.lang == lang))
            .outerjoin(ct_ru, and_(ct_ru.city_id == Clinic.city_id, ct_ru.lang == DEFAULT_LANG))
            # Агрегаты — сгруппированными подзапросами, а не скалярными на строку:
            # список админки читает сотни клиник, и коррелированный подзапрос на
            # каждую превратился бы в тот же N+1, только внутри SQL.
            .outerjoin(patients, patients.c.clinic_id == Clinic.id)
            .outerjoin(revenue, revenue.c.clinic_id == Clinic.id)
        )

    @staticmethod
    def _apply_filters(
        stmt: Select[Any],
        *,
        type_: ClinicType | None,
        status: SubscriptionStatus | None,
        city_ids: list[uuid.UUID] | None,
        tariff_names: list[str] | None,
        query: str | None,
    ) -> Select[Any]:
        if type_ is not None:
            stmt = stmt.where(Clinic.type == type_)
        if status is not None:
            stmt = stmt.where(Subscription.status == status)
        if city_ids:
            stmt = stmt.where(Clinic.city_id.in_(city_ids))
        if tariff_names:
            types = [_TYPE_BY_TARIFF_NAME[n] for n in tariff_names if n in _TYPE_BY_TARIFF_NAME]
            if types:
                stmt = stmt.where(Clinic.type.in_(types))
        if query:
            like = f"%{query}%"
            stmt = stmt.where(
                or_(
                    Clinic.name.ilike(like),
                    Account.full_name.ilike(like),
                    Account.phone.ilike(like),
                )
            )
        return stmt

    async def list_clients(
        self,
        *,
        lang: Lang,
        type_: ClinicType | None = None,
        status: SubscriptionStatus | None = None,
        city_ids: list[uuid.UUID] | None = None,
        tariff_names: list[str] | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[ClientListRow], int]:
        stmt = self._apply_filters(
            self._list_base(lang),
            type_=type_,
            status=status,
            city_ids=city_ids,
            tariff_names=tariff_names,
            query=query,
        )

        # Общее число под фильтром — count по тому же условию (без сортировки).
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.order_by(Clinic.created_at.desc(), Clinic.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        rows = (await self._session.execute(stmt)).all()
        items = [
            ClientListRow(
                id=r.id,
                type=r.type,
                name=r.name,
                admin_full_name=r.admin_full_name,
                admin_phone=r.admin_phone,
                admin_blocked_at=r.admin_blocked_at,
                city_id=r.city_id,
                city_name=r.city_name,
                seats_used=r.seats_used,
                seats_limit=r.seats_limit,
                subscription_status=r.subscription_status,
                trial_started_at=r.trial_started_at,
                current_period_end=r.current_period_end,
                card_attached_at=r.card_attached_at,
                created_at=r.created_at,
                patients_count=r.patients_count,
                revenue_tiyin=r.revenue_tiyin,
            )
            for r in rows
        ]
        return items, total

    async def status_counts(self) -> dict[SubscriptionStatus, int]:
        """Число клиентов в каждом статусе подписки (для дропдауна фильтра)."""
        rows = (
            await self._session.execute(
                select(Subscription.status, func.count())
                .select_from(Subscription)
                .group_by(Subscription.status)
            )
        ).all()
        counts: dict[SubscriptionStatus, int] = dict.fromkeys(SubscriptionStatus, 0)
        for status, count in rows:
            counts[status] = count
        return counts

    async def get_client(self, clinic_id: uuid.UUID, *, lang: Lang) -> ClientDetail | None:
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            return None
        city_name = await self._city_name(clinic.city_id, lang) if clinic.city_id else None
        staff = await self._staff(clinic_id, lang)
        subscription = (
            await self._session.execute(
                select(Subscription).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()
        seats_used = sum(1 for s in staff if s.active and not s.is_owner)
        patients_count, visits_count = await self._clinic_activity(clinic_id)
        return ClientDetail(
            clinic=clinic,
            city_name=city_name,
            subscription=subscription,
            staff=staff,
            seats_used=seats_used,
            patients_count=patients_count,
            visits_count=visits_count,
            revenue_tiyin=await self._clinic_revenue(clinic_id),
            whatsapp=(
                await self._session.execute(
                    select(ClinicWhatsApp).where(ClinicWhatsApp.clinic_id == clinic_id)
                )
            ).scalar_one_or_none(),
            custom_plan=(
                await self._session.execute(
                    select(ClinicCustomPlan).where(ClinicCustomPlan.clinic_id == clinic_id)
                )
            ).scalar_one_or_none(),
            open_plan_request=(
                await self._session.execute(
                    select(PlanRequest).where(
                        PlanRequest.clinic_id == clinic_id, PlanRequest.status == "new"
                    )
                )
            ).scalar_one_or_none(),
        )

    async def _clinic_activity(self, clinic_id: uuid.UUID) -> tuple[int, int]:
        """(пациентов, приёмов) клиники — блок активности в карточке."""
        row = (
            await self._session.execute(
                select(
                    func.count(distinct(Appointment.patient_id)),
                    func.count(Appointment.id),
                ).where(Appointment.clinic_id == clinic_id)
            )
        ).one()
        return int(row[0]), int(row[1])

    async def _clinic_revenue(self, clinic_id: uuid.UUID) -> int:
        """Нетто кассы клиники за всё время (платежи минус возвраты)."""
        signed = func.sum(
            case(
                (CashPayment.kind == PaymentKind.REFUND, -CashPayment.amount_tiyin),
                else_=CashPayment.amount_tiyin,
            )
        )
        value = (
            await self._session.execute(
                select(func.coalesce(signed, 0)).where(CashPayment.clinic_id == clinic_id)
            )
        ).scalar_one()
        return int(value)

    async def _city_name(self, city_id: uuid.UUID, lang: Lang) -> str | None:
        stmt = select(CityTranslation.value).where(
            CityTranslation.city_id == city_id, CityTranslation.lang == lang
        )
        value = (await self._session.execute(stmt)).scalar_one_or_none()
        if value is None and lang is not DEFAULT_LANG:
            stmt_ru = select(CityTranslation.value).where(
                CityTranslation.city_id == city_id, CityTranslation.lang == DEFAULT_LANG
            )
            value = (await self._session.execute(stmt_ru)).scalar_one_or_none()
        return value

    async def _staff(self, clinic_id: uuid.UUID, lang: Lang) -> list[StaffRow]:
        st_lang = aliased(SpecializationTranslation)
        st_ru = aliased(SpecializationTranslation)
        spec_name = func.coalesce(st_lang.value, st_ru.value)
        rows = (
            await self._session.execute(
                select(
                    Membership.id,
                    Membership.account_id,
                    Account.full_name,
                    Account.phone,
                    Membership.role_id,
                    Role.key,
                    Role.name,
                    Role.is_owner,
                    Membership.specialization_id,
                    spec_name.label("specialization_name"),
                    Membership.active,
                    Account.blocked_at,
                    Membership.created_at,
                )
                .join(Account, Account.id == Membership.account_id)
                .join(Role, Role.id == Membership.role_id)
                .outerjoin(
                    st_lang,
                    and_(
                        st_lang.specialization_id == Membership.specialization_id,
                        st_lang.lang == lang,
                    ),
                )
                .outerjoin(
                    st_ru,
                    and_(
                        st_ru.specialization_id == Membership.specialization_id,
                        st_ru.lang == DEFAULT_LANG,
                    ),
                )
                .where(Membership.clinic_id == clinic_id)
                .order_by(Role.is_owner.desc(), Membership.created_at)
            )
        ).all()
        return [
            StaffRow(
                membership_id=r[0],
                account_id=r[1],
                full_name=r[2],
                phone=r[3],
                role_id=r[4],
                role_key=r[5],
                role_name=r[6],
                is_owner=r[7],
                specialization_id=r[8],
                specialization_name=r[9],
                active=r[10],
                blocked_at=r[11],
                created_at=r[12],
            )
            for r in rows
        ]

    async def staff(
        self, clinic_id: uuid.UUID, lang: Lang, *, active: bool | None = None
    ) -> list[StaffRow]:
        """Сотрудники клиники (владелец + врачи), опционально только активные.

        Исключённые по умолчанию остаются в выдаче: на их членстве висят приёмы и
        платежи, и админке нужно видеть, кто их вёл. Фильтр ``active`` — способ
        посмотреть отдельно работающих или отдельно уволенных.
        """
        rows = await self._staff(clinic_id, lang)
        return rows if active is None else [r for r in rows if r.active is active]

    async def clinic_exists(self, clinic_id: uuid.UUID) -> bool:
        result = await self._session.execute(select(Clinic.id).where(Clinic.id == clinic_id))
        return result.scalar_one_or_none() is not None

    async def staff_member(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, lang: Lang
    ) -> StaffRow | None:
        """Один сотрудник клиники с резолвом названия специализации на язык."""
        staff = await self._staff(clinic_id, lang)
        return next((s for s in staff if s.membership_id == membership_id), None)

    async def city_exists(self, city_id: uuid.UUID) -> bool:
        result = await self._session.execute(select(City.id).where(City.id == city_id))
        return result.scalar_one_or_none() is not None

    async def specialization_exists(self, specialization_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(Specialization.id).where(Specialization.id == specialization_id)
        )
        return result.scalar_one_or_none() is not None

    async def update_client(self, clinic_id: uuid.UUID, fields: dict[str, Any]) -> Clinic | None:
        """Записать разрешённые поля клиники."""
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            return None
        apply_updates(
            clinic,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="клиента",
        )
        await self._session.flush()
        return clinic

    async def owner_account(self, clinic_id: uuid.UUID) -> Account | None:
        return (
            await self._session.execute(
                select(Account)
                .join(Membership, Membership.account_id == Account.id)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Role.is_owner.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def update_owner(
        self,
        clinic_id: uuid.UUID,
        *,
        full_name: str | None,
        specialization_id: uuid.UUID | None,
        set_specialization: bool,
    ) -> None:
        """Правка владельца: ФИО (account) и/или специализация (membership, соло)."""
        owner_membership = (
            await self._session.execute(
                select(Membership)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Role.is_owner.is_(True),
                )
            )
        ).scalar_one_or_none()
        if owner_membership is None:
            return
        if set_specialization:
            owner_membership.specialization_id = specialization_id
        if full_name is not None:
            account = await self._session.get(Account, owner_membership.account_id)
            if account is not None:
                account.full_name = full_name
        await self._session.flush()

    async def account_by_phone(self, phone: str) -> Account | None:
        return (
            await self._session.execute(select(Account).where(Account.phone == phone))
        ).scalar_one_or_none()

    async def owner_name_by_phone(self, phone: str) -> str | None:
        """ФИО владельца аккаунта с данным номером (для phone-check), либо None."""
        return (
            await self._session.execute(select(Account.full_name).where(Account.phone == phone))
        ).scalar_one_or_none()

    async def update_account_phone(self, account_id: uuid.UUID, phone: str) -> None:
        account = await self._session.get(Account, account_id)
        if account is not None:
            account.phone = phone
            await self._session.flush()

    async def archive(self, clinic_id: uuid.UUID, reason: str | None) -> bool:
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            return False
        subscription = (
            await self._session.execute(
                select(Subscription).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()
        if subscription is not None and subscription.status is not SubscriptionStatus.CANCELLED:
            from_status = subscription.status
            subscription.status = SubscriptionStatus.CANCELLED
            subscription.cancelled_at = datetime.now(UTC)
            self._session.add(
                SubscriptionEvent(
                    subscription_id=subscription.id,
                    from_status=from_status,
                    to_status=SubscriptionStatus.CANCELLED,
                    reason=reason or "manual_archive",
                )
            )
        await self._session.flush()
        return True
