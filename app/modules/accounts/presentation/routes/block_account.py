"""Роуты: POST /admin/accounts/{id}/block · /unblock — блокировка аккаунта."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.accounts.application.use_cases.block_account import BlockAccountUseCase
from app.modules.accounts.presentation.deps import get_block_account_use_case
from app.modules.accounts.presentation.schemas import AccountBlockedResponse, BlockAccountRequest
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Аккаунт не найден",
        "content": {"application/json": {"example": {"error": "account_not_found"}}},
    },
    409: {
        "description": "Попытка заблокировать себя",
        "content": {
            "application/json": {
                "example": {"error": "cannot_block_self", "message": "Нельзя заблокировать себя"}
            }
        },
    },
}


@router.post(
    "/admin/accounts/{account_id}/block",
    response_model=AccountBlockedResponse,
    summary="Заблокировать аккаунт",
    response_description="Состояние блокировки после операции",
    responses=_ERRORS,
)
async def block_account(
    account_id: uuid.UUID,
    body: BlockAccountRequest | None = None,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: BlockAccountUseCase = Depends(get_block_account_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AccountBlockedResponse:
    """Отобрать у человека доступ целиком — единственный способ это сделать.

    Исключение из клиники (`DELETE /admin/clients/{id}/doctors/{docId}`) закрывает
    только её: аккаунт глобальный, и уволенный по-прежнему войдёт в любую другую
    клинику, где числится. Блокировка не пускает вовсе — ни по коду, ни по паролю.

    Действует **сразу**: все сессии аккаунта гасятся, а уже выданный access-токен
    получает `403 account_blocked` на любой ручке обоих контуров.

    Себя заблокировать нельзя (`409 cannot_block_self`): иначе админ теряет доступ
    к ручке, которой блокировку снимают.
    """
    reason = body.reason if body is not None else None
    account = await use_case.block(account_id, actor_id=actor, reason=reason)
    await audit.record(
        actor_id=actor,
        action="account.blocked",
        entity_type="account",
        entity_id=account_id,
        payload={"reason": reason},
    )
    return AccountBlockedResponse.from_account(account)


@router.post(
    "/admin/accounts/{account_id}/unblock",
    response_model=AccountBlockedResponse,
    summary="Разблокировать аккаунт",
    response_description="Состояние блокировки после операции",
    responses={404: _ERRORS[404]},
)
async def unblock_account(
    account_id: uuid.UUID,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: BlockAccountUseCase = Depends(get_block_account_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AccountBlockedResponse:
    """Вернуть доступ. Сессии не восстанавливаются — входить нужно заново."""
    account = await use_case.unblock(account_id)
    await audit.record(
        actor_id=actor,
        action="account.unblocked",
        entity_type="account",
        entity_id=account_id,
    )
    return AccountBlockedResponse.from_account(account)
