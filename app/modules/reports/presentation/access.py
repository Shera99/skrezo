"""RBAC-скоуп отчётов: «вся клиника» против «только свои показатели».

* ``reports.view_all`` (владелец/менеджер) — отчёты по всей клинике;
* ``reports.view_own`` (врач) — только по своим приёмам.

«Своё» здесь — приёмы, где вызывающий **лечащий врач** (``doctor_membership``).
Это намеренно отличается от кассы, где «своё» = кто провёл платёж
(``recorded_by``): отчёт врача — про его работу, а не про то, кто принял деньги.

Платформенный контур (``/admin/reports/*``) этим скоупом не пользуется: там
доступ даёт ``is_platform_admin`` и ограничений по врачу нет.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends

from app.core.errors import ForbiddenError
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.application.services.subscription_policy import (
    DENY_MESSAGES,
    SubscriptionPolicy,
)
from app.modules.access.presentation.deps import (
    get_clinic_context,
    get_permission_resolver,
    get_subscription_policy,
)
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository
from app.modules.reports.presentation.deps import get_reports_repository
from app.shared.auth import ClientContext


@dataclass(frozen=True, slots=True)
class ReportsScope:
    """Разрешённый охват отчётов: вся клиника или только свои приёмы."""

    context: ClientContext
    unrestricted: bool
    own_membership_id: uuid.UUID | None

    @property
    def clinic_id(self) -> uuid.UUID:
        """Клиника из токена; гвард гарантирует, что она выбрана."""
        assert self.context.clinic_id is not None  # гарантировано гвардом
        return self.context.clinic_id


async def require_reports_read(
    context: ClientContext = Depends(get_clinic_context),
    resolver: PermissionResolver = Depends(get_permission_resolver),
    reports: ReportsRepository = Depends(get_reports_repository),
    policy: SubscriptionPolicy = Depends(get_subscription_policy),
) -> ReportsScope:
    """Гвард отчётов: view_all → вся клиника, иначе view_own → только свои приёмы."""
    if context.role_id is None or context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    resolved = await resolver.resolve(context.role_id)
    if resolved is None:
        raise ForbiddenError("Роль не найдена")

    unrestricted = resolved.is_owner or "reports.view_all" in resolved.permissions
    allowed = unrestricted or "reports.view_own" in resolved.permissions
    if not allowed:
        raise ForbiddenError("Недостаточно прав")
    effective_key = "reports.view_all" if unrestricted else "reports.view_own"
    reason = await policy.deny_reason(context.clinic_id, effective_key)
    if reason is not None:
        raise ForbiddenError(DENY_MESSAGES[reason], code=reason.value)

    own_membership_id = (
        None
        if unrestricted
        else await reports.own_membership(context.account_id, context.clinic_id)
    )
    if not unrestricted and own_membership_id is None:
        # Есть право view_own, но членства в клинике нет — показывать нечего,
        # а подставлять «всю клинику» нельзя.
        raise ForbiddenError("Нет доступа к отчётам", code="reports_forbidden")
    return ReportsScope(
        context=context, unrestricted=unrestricted, own_membership_id=own_membership_id
    )


def resolve_scope_doctor(scope: ReportsScope, requested: uuid.UUID | None) -> uuid.UUID | None:
    """Свести запрошенного врача к тому, чьи показатели вызывающему можно смотреть.

    * ``unrestricted`` (``view_all``) — как запрошено; ``None`` — вся клиника;
    * ``view_own`` — только собственное членство: чужой ``doctorMembershipId`` →
      403, пустой — подставляется своё. Иначе врач, отфильтровав «по врачу»,
      обошёл бы сужение до своих приёмов.
    """
    if scope.unrestricted:
        return requested
    own = scope.own_membership_id
    if requested is not None and requested != own:
        raise ForbiddenError("Доступны только свои показатели", code="not_own_report")
    return own
