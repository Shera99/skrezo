"""Use-case: обновление клиентского access-токена."""

from __future__ import annotations

from app.modules.identity.application.services.client_token_service import ClientTokenService


class RefreshTokenUseCase:
    def __init__(self, tokens: ClientTokenService) -> None:
        self._tokens = tokens

    async def execute(self, refresh_token: str) -> tuple[str, int]:
        return await self._tokens.refresh_access(refresh_token)
