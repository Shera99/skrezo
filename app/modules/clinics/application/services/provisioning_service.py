"""Провижининг: атомарное создание клиники и всего сопутствующего.

Один транзакционный сценарий: клиника → клонирование ролей → аккаунт-владелец
→ членство(owner) → подписка(trial). Используется ручным созданием из админки
Skrezo (клиники заводит только команда). Дедуп телефона по аккаунтам.

Владельцу выдаётся **временный пароль** (`must_change_password=true`): он
возвращается в результате один раз, чтобы админ передал его владельцу; при первом
входе владелец обязан сменить пароль.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationAppError
from app.core.security import generate_temp_password, hash_password
from app.modules.access.application.services.role_provisioning_service import (
    RoleProvisioningService,
)
from app.modules.clinics.infrastructure.repositories.account_repository import AccountRepository
from app.modules.clinics.infrastructure.repositories.clinic_repository import ClinicRepository
from app.modules.clinics.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.modules.leads.infrastructure.repositories.lead_repository import LeadRepository
from app.modules.subscriptions.application.services.subscription_service import SubscriptionService
from app.shared.db.enums import ClinicType, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.domain.value_objects import normalize_phone


@dataclass(slots=True)
class ProvisionCommand:
    type: ClinicType
    admin_full_name: str
    admin_phone: str
    city_id: uuid.UUID | None = None
    clinic_name: str | None = None  # обяз. для clinic; для solo = имя врача
    address: str | None = None
    business_phone: str | None = None
    seats_limit: int | None = None
    lang: Lang = Lang.RU
    lead_id: uuid.UUID | None = None


@dataclass(slots=True)
class ProvisionResult:
    clinic: Clinic
    account: Account
    owner_role_id: uuid.UUID
    subscription_id: uuid.UUID
    temp_password: str  # временный пароль владельца (показать один раз)


class ProvisioningService:
    def __init__(
        self,
        session: AsyncSession,
        clinics: ClinicRepository,
        accounts: AccountRepository,
        memberships: MembershipRepository,
        roles: RoleProvisioningService,
        subscriptions: SubscriptionService,
        leads: LeadRepository,
    ) -> None:
        self._session = session
        self._clinics = clinics
        self._accounts = accounts
        self._memberships = memberships
        self._roles = roles
        self._subscriptions = subscriptions
        self._leads = leads

    async def provision(self, command: ProvisionCommand) -> ProvisionResult:
        phone = normalize_phone(command.admin_phone)
        if await self._accounts.exists_by_phone(phone):
            raise ConflictError("Этот номер уже используется", code="phone_already_used")
        if command.city_id is not None and not await self._clinics.city_exists(command.city_id):
            raise ValidationAppError("Город не найден", code="city_not_found")

        # Для соло название клиники = имя врача (отдельного названия нет).
        clinic_name = command.clinic_name or command.admin_full_name
        clinic = await self._clinics.create(
            name=clinic_name,
            type=command.type,
            city_id=command.city_id,
            address=command.address,
            business_phone=command.business_phone,
            seats_limit=command.seats_limit,
            lang=command.lang,
        )
        owner_role_id = await self._roles.clone_templates_for_clinic(clinic.id)
        temp_password = generate_temp_password()
        account = await self._accounts.create(
            phone=phone,
            full_name=command.admin_full_name,
            lang=command.lang,
            onboarded=False,
            password_hash=hash_password(temp_password),
        )
        await self._memberships.create(
            clinic_id=clinic.id, account_id=account.id, role_id=owner_role_id
        )
        subscription = await self._subscriptions.create_trial(clinic.id)
        if command.lead_id is not None:
            await self._leads.mark_converted(command.lead_id, clinic.id)

        await self._session.commit()
        return ProvisionResult(clinic, account, owner_role_id, subscription.id, temp_password)
