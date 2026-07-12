"""Общий слой авторизации: контекст клиентского токена.

Живёт в shared, чтобы модули ``identity`` (выпуск токена) и ``access``
(проверка прав) зависели от него, а не друг от друга.
"""

from __future__ import annotations

from app.shared.auth.context import ClientContext, get_client_context

__all__ = ["ClientContext", "get_client_context"]
