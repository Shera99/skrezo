"""Зависимости RBAC: резолвер прав, триал-политика и guard ``require_permission``.

``require_permission("key")`` — фабрика FastAPI-зависимости для защиты ручек
клиентского контура. Владелец (``is_owner``) проходит проверку прав роли, но на
триале и он ограничен набором триал-функций: эффективный доступ =
(право у роли ИЛИ владелец) И (клиника не на триале ИЛИ право входит в триал).

Все клиничные гварды входят через :func:`get_clinic_context`, а не через голый
``get_client_context``: роль должна браться из живого членства, иначе токен
уволенного сотрудника работает до конца своей жизни.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError
from app.core.redis import get_redis
from app.modules.access.application.services.membership_resolver import MembershipResolver
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.application.services.subscription_policy import (
    DENY_MESSAGES,
    SubscriptionPolicy,
)
from app.modules.access.application.use_cases.get_trial_features import GetTrialFeaturesUseCase
from app.modules.access.application.use_cases.manage_roles import (
    CreateRoleUseCase,
    DeleteRoleUseCase,
    ListPermissionsUseCase,
    ListRolesUseCase,
    UpdateRoleUseCase,
)
from app.modules.access.application.use_cases.update_trial_features import (
    UpdateTrialFeaturesUseCase,
)
from app.modules.access.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.modules.access.infrastructure.repositories.role_admin_repository import (
    RoleAdminRepository,
)
from app.modules.access.infrastructure.repositories.role_repository import RoleRepository
from app.modules.access.infrastructure.repositories.subscription_policy_repository import (
    SubscriptionPolicyRepository,
)
from app.shared.auth import ClientContext, get_client_context


def get_role_repository(session: AsyncSession = Depends(get_session)) -> RoleRepository:
    return RoleRepository(session)


def get_permission_resolver(
    role_repository: RoleRepository = Depends(get_role_repository),
    redis: Redis = Depends(get_redis),
) -> PermissionResolver:
    return PermissionResolver(role_repository, redis)


def get_membership_repository(session: AsyncSession = Depends(get_session)) -> MembershipRepository:
    return MembershipRepository(session)


def get_membership_resolver(
    memberships: MembershipRepository = Depends(get_membership_repository),
    redis: Redis = Depends(get_redis),
) -> MembershipResolver:
    return MembershipResolver(memberships, redis)


async def get_clinic_context(
    context: ClientContext = Depends(get_client_context),
    memberships: MembershipResolver = Depends(get_membership_resolver),
) -> ClientContext:
    """Контекст клиничного запроса с ролью из **живого** членства.

    Точка входа всех клиничных гвардов. Клиника берётся из токена (её выбирал
    сам сотрудник и валидировал ``select-clinic``), а роль — из базы: claim
    ``role_id`` выписан один раз и не знает ни об увольнении, ни о смене роли.

    Уволенному отвечаем отдельным кодом ``membership_revoked``, а не общим
    «недостаточно прав»: клиенту по нему видно, что дело не в конкретной ручке и
    сессию пора закрывать.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    role_id = await memberships.resolve(context.account_id, context.clinic_id)
    if role_id is None:
        raise ForbiddenError("Доступ к клинике закрыт", code="membership_revoked")
    return dataclasses.replace(context, role_id=role_id)


def get_subscription_policy_repository(
    session: AsyncSession = Depends(get_session),
) -> SubscriptionPolicyRepository:
    return SubscriptionPolicyRepository(session)


def get_subscription_policy(
    repository: SubscriptionPolicyRepository = Depends(get_subscription_policy_repository),
    redis: Redis = Depends(get_redis),
) -> SubscriptionPolicy:
    return SubscriptionPolicy(repository, redis)


def get_get_trial_features_use_case(
    repository: SubscriptionPolicyRepository = Depends(get_subscription_policy_repository),
) -> GetTrialFeaturesUseCase:
    return GetTrialFeaturesUseCase(repository)


def get_update_trial_features_use_case(
    session: AsyncSession = Depends(get_session),
    repository: SubscriptionPolicyRepository = Depends(get_subscription_policy_repository),
    policy: SubscriptionPolicy = Depends(get_subscription_policy),
) -> UpdateTrialFeaturesUseCase:
    return UpdateTrialFeaturesUseCase(session, repository, policy)


def has_permission(permission_key: str) -> Callable[..., Awaitable[bool]]:
    """Вернуть зависимость-признак «есть ли право» — без отказа в доступе.

    Нужна там, где право включает дополнительную возможность внутри уже
    разрешённой ручки (например откат статуса приёма при обычной правке), а не
    открывает саму ручку.
    """

    async def _dep(
        context: ClientContext = Depends(get_clinic_context),
        resolver: PermissionResolver = Depends(get_permission_resolver),
        policy: SubscriptionPolicy = Depends(get_subscription_policy),
    ) -> bool:
        if context.role_id is None:
            return False
        resolved = await resolver.resolve(context.role_id)
        if resolved is None:
            return False
        granted = resolved.is_owner or permission_key in resolved.permissions
        return granted and await policy.permits(context.clinic_id, permission_key)

    return _dep


async def get_discount_ceiling(
    context: ClientContext = Depends(get_clinic_context),
    resolver: PermissionResolver = Depends(get_permission_resolver),
) -> int:
    """Потолок скидки текущего сотрудника (%): владелец 100, иначе — из роли.

    Отдельно от ``require_permission("cash.discount")``: право отвечает на вопрос
    «можно ли давать скидку вообще», а это число — «насколько». Врач с правом,
    но с нулевым потолком не скинет ничего, и это осознанная настройка роли.
    """
    if context.role_id is None:
        return 0
    resolved = await resolver.resolve(context.role_id)
    return resolved.discount_ceiling if resolved is not None else 0


def require_permission(
    permission_key: str,
) -> Callable[..., Awaitable[ClientContext]]:
    """Вернуть зависимость, требующую у текущего клиента право ``permission_key``."""

    async def _guard(
        context: ClientContext = Depends(get_clinic_context),
        resolver: PermissionResolver = Depends(get_permission_resolver),
        policy: SubscriptionPolicy = Depends(get_subscription_policy),
    ) -> ClientContext:
        if context.role_id is None:
            raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
        resolved = await resolver.resolve(context.role_id)
        if resolved is None:
            raise ForbiddenError("Роль не найдена")
        if not (resolved.is_owner or permission_key in resolved.permissions):
            raise ForbiddenError("Недостаточно прав")
        # На триале доступны только функции из списка админки, а у клиники с
        # закрытой подпиской (истёк триал, просрочка, блок) — только чтение.
        reason = await policy.deny_reason(context.clinic_id, permission_key)
        if reason is not None:
            raise ForbiddenError(DENY_MESSAGES[reason], code=reason.value)
        return context

    return _guard


def require_any_permission(
    *permission_keys: str,
) -> Callable[..., Awaitable[ClientContext]]:
    """Зависимость: у текущего клиента есть ХОТЯ БЫ ОДНО из прав ``permission_keys``.

    Для экранов на стыке ролей: состояние номера WhatsApp видят и владелец
    (``whatsapp.manage``), и администратор переписки (``conversations.manage``).
    Ограничения триала и закрытой подписки проверяются по выданному праву.
    """

    async def _guard(
        context: ClientContext = Depends(get_clinic_context),
        resolver: PermissionResolver = Depends(get_permission_resolver),
        policy: SubscriptionPolicy = Depends(get_subscription_policy),
    ) -> ClientContext:
        if context.role_id is None:
            raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
        resolved = await resolver.resolve(context.role_id)
        if resolved is None:
            raise ForbiddenError("Роль не найдена")
        granted = [
            key for key in permission_keys if resolved.is_owner or key in resolved.permissions
        ]
        if not granted:
            raise ForbiddenError("Недостаточно прав")
        reasons = [await policy.deny_reason(context.clinic_id, key) for key in granted]
        if all(reason is not None for reason in reasons):
            first = reasons[0]
            assert first is not None
            raise ForbiddenError(DENY_MESSAGES[first], code=first.value)
        return context

    return _guard


def get_role_admin_repository(
    session: AsyncSession = Depends(get_session),
) -> RoleAdminRepository:
    return RoleAdminRepository(session)


def get_list_roles_use_case(
    roles: RoleAdminRepository = Depends(get_role_admin_repository),
) -> ListRolesUseCase:
    return ListRolesUseCase(roles)


def get_list_permissions_use_case(
    roles: RoleAdminRepository = Depends(get_role_admin_repository),
) -> ListPermissionsUseCase:
    return ListPermissionsUseCase(roles)


def get_create_role_use_case(
    session: AsyncSession = Depends(get_session),
    roles: RoleAdminRepository = Depends(get_role_admin_repository),
    resolver: PermissionResolver = Depends(get_permission_resolver),
) -> CreateRoleUseCase:
    return CreateRoleUseCase(session, roles, resolver)


def get_update_role_use_case(
    session: AsyncSession = Depends(get_session),
    roles: RoleAdminRepository = Depends(get_role_admin_repository),
    resolver: PermissionResolver = Depends(get_permission_resolver),
) -> UpdateRoleUseCase:
    return UpdateRoleUseCase(session, roles, resolver)


def get_delete_role_use_case(
    session: AsyncSession = Depends(get_session),
    roles: RoleAdminRepository = Depends(get_role_admin_repository),
    resolver: PermissionResolver = Depends(get_permission_resolver),
) -> DeleteRoleUseCase:
    return DeleteRoleUseCase(session, roles, resolver)
