"""Use-case: выход клиента (отзыв refresh-токена)."""

from __future__ import annotations

from app.modules.identity.application.services.client_token_service import ClientTokenService


class LogoutUseCase:
    def __init__(self, tokens: ClientTokenService) -> None:
        self._tokens = tokens

    async def execute(self, refresh_token: str) -> None:
        await self._tokens.revoke(refresh_token)
