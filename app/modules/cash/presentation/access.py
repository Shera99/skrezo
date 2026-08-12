"""RBAC-скоуп кассы: «вся клиника» против «только своё».

Чтение журнала/сводки/долгов: право ``cash.view_all`` (владелец/менеджер) видит
кассу всей клиники, право ``cash.view_own`` (кассир/врач) — только своё. «Своё» —
собственное членство в клинике из токена: для платежей это ``recorded_by`` (кто
провёл), для долгов — ``doctor_membership`` (по чьим приёмам долг).

Проведение платежей/возвратов защищено правом ``cash.operate`` через общий
``require_permission`` — отдельного скоупа не нужно: ``recorded_by`` кассовый
сервис проставляет по членству вызывающего.
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
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository
from app.modules.cash.presentation.deps import get_cash_repository
from app.shared.auth import ClientContext


@dataclass(frozen=True, slots=True)
class CashReadScope:
    """Разрешённый охват кассы для чтения.

    ``unrestricted`` — вся клиника (view_all или владелец). Иначе только своё
    членство ``own_membership_id``.
    """

    context: ClientContext
    unrestricted: bool
    own_membership_id: uuid.UUID | None

    @property
    def clinic_id(self) -> uuid.UUID:
        """Клиника из токена; гвард гарантирует, что она выбрана."""
        assert self.context.clinic_id is not None  # гарантировано гвардом
        return self.context.clinic_id


async def require_cash_read(
    context: ClientContext = Depends(get_clinic_context),
    resolver: PermissionResolver = Depends(get_permission_resolver),
    cash: CashRepository = Depends(get_cash_repository),
    policy: SubscriptionPolicy = Depends(get_subscription_policy),
) -> CashReadScope:
    """Гвард чтения кассы: view_all → вся клиника, иначе view_own → только своё."""
    if context.role_id is None or context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    resolved = await resolver.resolve(context.role_id)
    if resolved is None:
        raise ForbiddenError("Роль не найдена")

    unrestricted = resolved.is_owner or "cash.view_all" in resolved.permissions
    allowed = unrestricted or "cash.view_own" in resolved.permissions
    if not allowed:
        raise ForbiddenError("Недостаточно прав")
    effective_key = "cash.view_all" if unrestricted else "cash.view_own"
    reason = await policy.deny_reason(context.clinic_id, effective_key)
    if reason is not None:
        raise ForbiddenError(DENY_MESSAGES[reason], code=reason.value)

    own_membership_id = (
        None if unrestricted else await cash.own_membership(context.account_id, context.clinic_id)
    )
    return CashReadScope(
        context=context, unrestricted=unrestricted, own_membership_id=own_membership_id
    )
