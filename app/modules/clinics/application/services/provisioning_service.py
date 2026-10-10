"""Провижининг: атомарное создание клиники и всего сопутствующего.

Один транзакционный сценарий: клиника → клонирование ролей → аккаунт-владелец
→ членство(owner) → подписка(trial). Используется ручным созданием из админки
Skrezo (клиники заводит только команда) и самостоятельной регистрацией соло-врача.
Дедуп телефона по аккаунтам.

Если передана заявка из очереди (``application_id``), она берётся под блокировку
до создания чего-либо и в конце той же транзакции помечается сконвертированной
и рассмотренной.

Аккаунт владельца создаётся **без пароля**: и клиника, и соло входят по коду на
телефон. Передавать при заведении нечего — владелец сразу запрашивает код.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationAppError
from app.modules.access.application.services.role_provisioning_service import (
    RoleProvisioningService,
)
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.modules.clinics.infrastructure.repositories.account_repository import AccountRepository
from app.modules.clinics.infrastructure.repositories.clinic_repository import ClinicRepository
from app.modules.clinics.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.modules.subscriptions.application.services.subscription_service import SubscriptionService
from app.shared.db.enums import ClinicApplicationStatus, ClinicType, Lang
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
    # Конвертируемая заявка из очереди (опц.): помечается сконвертированной и рассмотренной.
    application_id: uuid.UUID | None = None
    # Кто из команды Skrezo заводит клиента; ставит только админский путь. Нужен,
    # чтобы записать рассмотревшего, когда заявка из pending/rejected становится reviewed.
    actor_account_id: uuid.UUID | None = None
    # Специализация владельца (соло-врач = practitioner); NULL для клиники.
    specialization_id: uuid.UUID | None = None


@dataclass(slots=True)
class ProvisionResult:
    clinic: Clinic
    account: Account
    owner_role_id: uuid.UUID
    subscription_id: uuid.UUID
    # Статус заявки до конверсии; None — заявка не передавалась. Нужен роуту,
    # чтобы записать в аудит смену статуса (если она была).
    application_status_before: ClinicApplicationStatus | None = None


class ProvisioningService:
    def __init__(
        self,
        session: AsyncSession,
        clinics: ClinicRepository,
        accounts: AccountRepository,
        memberships: MembershipRepository,
        roles: RoleProvisioningService,
        subscriptions: SubscriptionService,
        applications: ClinicApplicationRepository,
    ) -> None:
        self._session = session
        self._clinics = clinics
        self._accounts = accounts
        self._memberships = memberships
        self._roles = roles
        self._subscriptions = subscriptions
        self._applications = applications

    async def provision(self, command: ProvisionCommand) -> ProvisionResult:
        phone = normalize_phone(command.admin_phone)
        if await self._accounts.exists_by_phone(phone):
            raise ConflictError("Этот номер уже используется", code="phone_already_used")
        if command.city_id is not None and not await self._clinics.city_exists(command.city_id):
            raise ValidationAppError("Город не найден", code="city_not_found")
        if command.specialization_id is not None and not await self._clinics.specialization_exists(
            command.specialization_id
        ):
            raise ValidationAppError("Специализация не найдена", code="specialization_not_found")
        if command.application_id is not None:
            await self._lock_application(command.application_id)

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
        account = await self._accounts.create(
            phone=phone,
            full_name=command.admin_full_name,
            lang=command.lang,
            onboarded=False,
        )
        await self._memberships.create(
            clinic_id=clinic.id,
            account_id=account.id,
            role_id=owner_role_id,
            specialization_id=command.specialization_id,
        )
        subscription = await self._subscriptions.create_trial(clinic.id)
        application_status_before: ClinicApplicationStatus | None = None
        if command.application_id is not None:
            application_status_before = await self._applications.mark_converted(
                command.application_id, clinic.id, command.actor_account_id
            )

        await self._session.commit()
        return ProvisionResult(
            clinic,
            account,
            owner_role_id,
            subscription.id,
            application_status_before=application_status_before,
        )

    async def _lock_application(self, application_id: uuid.UUID) -> None:
        """Взять заявку под блокировку и убедиться, что по ней можно завести клиента.

        Блокировка (``SELECT ... FOR UPDATE``) держится до коммита провижининга и
        сериализует два одновременных заведения по одной заявке: второй дождётся
        первого, увидит ``converted_clinic_id`` и получит 409, ничего не создав.
        Проверка стоит до создания клиники, поэтому при отказе в сессии нет
        частично созданных объектов. Статус ``rejected`` не мешает: команда вправе
        передумать.
        """
        application = await self._applications.lock_for_conversion(application_id)
        if application is None:
            raise ValidationAppError("Заявка не найдена", code="application_not_found")
        if application.converted_clinic_id is not None:
            raise ConflictError(
                "По этой заявке клиент уже заведён",
                code="application_already_converted",
                extra={"clinicId": str(application.converted_clinic_id)},
            )


def build_provisioning_service(session: AsyncSession) -> ProvisioningService:
    """Собрать провижининг поверх сессии (все репозитории — одна транзакция).

    Провайдер в application-слое, чтобы соседний модуль ``identity`` (соло-
    регистрация) брал сервис отсюда, а не тянул ``clinics.presentation.deps``.
    """
    from app.modules.access.infrastructure.repositories.role_provisioning_repository import (
        RoleProvisioningRepository,
    )
    from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
        ClinicApplicationRepository,
    )
    from app.modules.clinics.infrastructure.repositories.account_repository import AccountRepository
    from app.modules.clinics.infrastructure.repositories.clinic_repository import ClinicRepository
    from app.modules.clinics.infrastructure.repositories.membership_repository import (
        MembershipRepository,
    )
    from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
        SubscriptionRepository,
    )

    return ProvisioningService(
        session=session,
        clinics=ClinicRepository(session),
        accounts=AccountRepository(session),
        memberships=MembershipRepository(session),
        roles=RoleProvisioningService(RoleProvisioningRepository(session)),
        subscriptions=SubscriptionService(SubscriptionRepository(session)),
        applications=ClinicApplicationRepository(session),
    )
