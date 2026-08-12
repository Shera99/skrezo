"""Репозиторий списка аккаунтов платформы (админ-сайд).

Читает всех людей разом — и команду Skrezo, и сотрудников клиник, — потому что
таблица ``account`` единая с миграции 0017.

Членства грузятся вторым запросом по идентификаторам страницы, а не джойном к
основному: джойн размножил бы строки на число клиник и сломал бы и ``LIMIT``, и
подсчёт ``totalCount``.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from contextlib import suppress
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.domain.models import AccountMembershipRow, AccountRow
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.domain.value_objects import normalize_phone


def _phone_conditions(query: str) -> list[Any]:
    """Условия поиска по телефону: как ввели и как лежит в базе.

    Телефоны хранятся в E.164 (``+996700123456``), а ищут их так, как диктуют, —
    ``0700123456``. Подстрокой одно в другом не находится: в базе после ``+996``
    нет ведущего нуля. Поэтому к поиску по подстроке добавляется точное
    совпадение с нормализованным номером.
    """
    conditions: list[Any] = [Account.phone.ilike(f"%{query}%")]
    with suppress(Exception):  # не телефон — остаётся только поиск по подстроке
        conditions.append(Account.phone == normalize_phone(query))
    return conditions


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _membership_exists(clinic_id: uuid.UUID | None, active: bool | None) -> Any:
        """EXISTS-условие «у аккаунта есть членство» с нужными оговорками.

        Отдельным подзапросом, а не джойном: нужен факт наличия, а не строки.
        """
        stmt = select(Membership.id).where(Membership.account_id == Account.id)
        if clinic_id is not None:
            stmt = stmt.where(Membership.clinic_id == clinic_id)
        if active is not None:
            stmt = stmt.where(Membership.active.is_(active))
        return stmt.exists()

    def _base(
        self,
        *,
        query: str | None,
        clinic_id: uuid.UUID | None,
        active: bool | None,
        is_platform_admin: bool | None,
    ) -> Select[Any]:
        stmt = select(Account)
        if query:
            stmt = stmt.where(
                or_(Account.full_name.ilike(f"%{query}%"), *_phone_conditions(query))
            )
        if is_platform_admin is not None:
            stmt = stmt.where(Account.is_platform_admin.is_(is_platform_admin))

        if active is None:
            if clinic_id is not None:
                stmt = stmt.where(self._membership_exists(clinic_id, None))
        elif active:
            # «Активен» — есть хотя бы одно живое членство (в указанной клинике,
            # если она задана).
            stmt = stmt.where(self._membership_exists(clinic_id, True))
        elif clinic_id is not None:
            # «Исключён из этой клиники» — членство в ней есть, но неживое.
            stmt = stmt.where(
                self._membership_exists(clinic_id, None),
                ~self._membership_exists(clinic_id, True),
            )
        else:
            # «Исключён отовсюду» — живых членств нет вовсе. Сюда же попадает
            # команда Skrezo: членств у неё нет по устройству, поэтому её
            # отсекают фильтром isPlatformAdmin, а не этим условием.
            stmt = stmt.where(~self._membership_exists(None, True))
        return stmt

    async def list_accounts(
        self,
        *,
        query: str | None = None,
        clinic_id: uuid.UUID | None = None,
        active: bool | None = None,
        is_platform_admin: bool | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[AccountRow], int]:
        stmt = self._base(
            query=query,
            clinic_id=clinic_id,
            active=active,
            is_platform_admin=is_platform_admin,
        )
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        accounts = (
            (
                await self._session.execute(
                    stmt.order_by(Account.created_at.desc(), Account.id.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        if not accounts:
            return [], total

        memberships = await self._memberships([a.id for a in accounts])
        return [
            AccountRow(
                id=a.id,
                full_name=a.full_name,
                phone=a.phone,
                lang=a.lang,
                onboarded=a.onboarded,
                is_platform_admin=a.is_platform_admin,
                created_at=a.created_at,
                memberships=memberships.get(a.id, []),
            )
            for a in accounts
        ], total

    async def _memberships(
        self, account_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[AccountMembershipRow]]:
        """Членства всех аккаунтов страницы одним запросом (иначе N+1)."""
        rows = (
            await self._session.execute(
                select(
                    Membership.account_id,
                    Membership.id,
                    Membership.clinic_id,
                    Clinic.name,
                    Membership.role_id,
                    Role.name,
                    Role.is_owner,
                    Membership.active,
                    Membership.created_at,
                )
                .join(Clinic, Clinic.id == Membership.clinic_id)
                .join(Role, Role.id == Membership.role_id)
                .where(Membership.account_id.in_(account_ids))
                # Живые выше исключённых, дальше — по названию клиники.
                .order_by(Membership.active.desc(), Clinic.name)
            )
        ).all()

        grouped: dict[uuid.UUID, list[AccountMembershipRow]] = defaultdict(list)
        for r in rows:
            grouped[r[0]].append(
                AccountMembershipRow(
                    membership_id=r[1],
                    clinic_id=r[2],
                    clinic_name=r[3],
                    role_id=r[4],
                    role_name=r[5],
                    is_owner=r[6],
                    active=r[7],
                    created_at=r[8],
                )
            )
        return grouped
