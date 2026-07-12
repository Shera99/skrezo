"""Платформенный гвард: доступ к /admin/* только для сотрудников Skrezo.

Единый вход (телефон + пароль) выдаёт один токен всем; команда Skrezo помечена
claim'ом ``platform_admin`` (см. `account.is_platform_admin`). Этот гвард поверх
общего контекста токена требует именно платформенного админа и возвращает его
``account.id`` (используется как actor в аудите).
"""

from __future__ import annotations

import uuid

from fastapi import Depends

from app.core.errors import ForbiddenError
from app.shared.auth import ClientContext, get_client_context


def get_authenticated_user_id(
    context: ClientContext = Depends(get_client_context),
) -> uuid.UUID:
    """Требовать платформенного админа Skrezo; вернуть его account_id."""
    if not context.is_platform_admin:
        raise ForbiddenError("Требуется доступ администратора Skrezo", code="not_platform_admin")
    return context.account_id
