"""RBAC-скоуп расписания: «все врачи» против «только своё».

Роль владельца/менеджера видит и правит расписание всех врачей
(``schedule.view_all`` / ``schedule.manage_all``). Роль врача видит и правит
только своё (``schedule.view_own`` / ``schedule.manage_own``) — по собственному
членству в клинике из токена.

Гварды :func:`require_schedule_read` и :func:`require_schedule_manage` пускают
запрос, если у роли есть любое из прав своего контура, и отдают
:class:`ScheduleScope` — «безграничный» ли доступ и id собственного членства.
Приведение параметра ``doctorId`` к разрешённому врачу — :func:`resolve_scope_doctor`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends

from app.core.errors import ForbiddenError
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.application.services.subscription_policy import (
    DENY_MESSAGES,
    SubscriptionPolicy,
)
from app.modules.access.presentation.deps import get_permission_resolver, get_subscription_policy
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.presentation.deps import get_schedule_repository
from app.shared.auth import ClientContext, get_client_context


@dataclass(frozen=True, slots=True)
class ScheduleScope:
    """Разрешённый охват расписания для текущего запроса.

    ``unrestricted`` — доступ ко всем врачам клиники (view_all/manage_all или
    владелец). Иначе доступ ограничен собственным членством ``own_membership_id``.
    """

    context: ClientContext
    unrestricted: bool
    own_membership_id: uuid.UUID | None

    @property
    def clinic_id(self) -> uuid.UUID:
        """Клиника из токена; гвард гарантирует, что она выбрана."""
        assert self.context.clinic_id is not None  # гарантировано гвардом
        return self.context.clinic_id


def _scope_guard(all_key: str, own_key: str) -> Callable[..., Awaitable[ScheduleScope]]:
    """Собрать зависимость-гвард для пары прав «все»/«свои»."""

    async def _guard(
        context: ClientContext = Depends(get_client_context),
        resolver: PermissionResolver = Depends(get_permission_resolver),
        schedule: ScheduleRepository = Depends(get_schedule_repository),
        policy: SubscriptionPolicy = Depends(get_subscription_policy),
    ) -> ScheduleScope:
        if context.role_id is None or context.clinic_id is None:
            raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
        resolved = await resolver.resolve(context.role_id)
        if resolved is None:
            raise ForbiddenError("Роль не найдена")

        unrestricted = resolved.is_owner or all_key in resolved.permissions
        allowed = unrestricted or own_key in resolved.permissions
        if not allowed:
            raise ForbiddenError("Недостаточно прав")
        # Триал-функции и режим чтения — по эффективному праву контура.
        effective_key = all_key if unrestricted else own_key
        reason = await policy.deny_reason(context.clinic_id, effective_key)
        if reason is not None:
            raise ForbiddenError(DENY_MESSAGES[reason], code=reason.value)

        # Собственное членство нужно только own-контуру; для view_all не тратим запрос.
        own_membership_id = (
            None
            if unrestricted
            else await schedule.own_membership(context.account_id, context.clinic_id)
        )
        return ScheduleScope(
            context=context,
            unrestricted=unrestricted,
            own_membership_id=own_membership_id,
        )

    return _guard


require_schedule_read = _scope_guard("schedule.view_all", "schedule.view_own")
"""Гвард чтения расписания: view_all → все врачи, иначе view_own → только своё."""

require_schedule_manage = _scope_guard("schedule.manage_all", "schedule.manage_own")
"""Гвард управления расписанием: manage_all → все врачи, иначе manage_own → только своё."""


def resolve_scope_doctor(
    scope: ScheduleScope, requested: uuid.UUID | None
) -> uuid.UUID | None:
    """Свести запрошенного врача к тому, кого вызывающему реально можно смотреть/править.

    * ``unrestricted`` — как запрошено (``None`` для ``/day`` = все врачи).
    * own-контур — только собственное членство: чужой ``doctorId`` → 403,
      пустой ``doctorId`` подставляет своё. Возвращаемое значение здесь никогда не
      ``None`` — иначе ``/day`` показал бы всех врачей вместо своей колонки.
    """
    if scope.unrestricted:
        return requested
    own = scope.own_membership_id
    if own is None:
        # Есть право view_own/manage_own, но собственного членства в клинике нет —
        # смотреть/править нечего и подставлять «всех» нельзя.
        raise ForbiddenError("Нет доступа к расписанию", code="schedule_forbidden")
    if requested is not None and requested != own:
        raise ForbiddenError("Доступно только своё расписание", code="not_own_schedule")
    return own
