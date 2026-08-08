"""Зависимости RBAC: резолвер прав, триал-политика и guard ``require_permission``.

``require_permission("key")`` — фабрика FastAPI-зависимости для защиты ручек
клиентского контура. Владелец (``is_owner``) проходит проверку прав роли, но на
триале и он ограничен набором триал-функций: эффективный доступ =
(право у роли ИЛИ владелец) И (клиника не на триале ИЛИ право входит в триал).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError
from app.core.redis import get_redis
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
        context: ClientContext = Depends(get_client_context),
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
    context: ClientContext = Depends(get_client_context),
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
        context: ClientContext = Depends(get_client_context),
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
