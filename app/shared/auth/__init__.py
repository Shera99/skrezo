"""Общий слой авторизации: контекст клиентского токена.

Живёт в shared, чтобы модули ``identity`` (выпуск токена) и ``access``
(проверка прав) зависели от него, а не друг от друга.
"""

from __future__ import annotations

from app.shared.auth.blocked import AccountBlockResolver, get_account_block_resolver
from app.shared.auth.context import ClientContext, get_client_context

__all__ = [
    "AccountBlockResolver",
    "ClientContext",
    "get_account_block_resolver",
    "get_client_context",
]
