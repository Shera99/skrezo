"""Шифрование секретов в базе: токены Meta клиник и подобное.

Секрет, который нужен приложению в открытом виде (токен для вызовов Graph
API), нельзя хешировать — только шифровать. Fernet (AES-128-CBC + HMAC-SHA256)
из ``cryptography``: шифротекст содержит время и проверку целостности, подмена
в базе даёт ошибку, а не мусорный токен.

Ключи — ``DATA_ENCRYPTION_KEY``, через запятую: шифрует первый, расшифровывают
все (``MultiFernet``). Ротация без простоя: дописать новый ключ первым,
перешифровать строки, убрать старый.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings


class SecretBoxError(Exception):
    """Ключ не задан, неверен или шифротекст повреждён."""


class SecretBox:
    """Шифрование и расшифровка строк набором ключей Fernet."""

    def __init__(self, keys: str) -> None:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        try:
            self._fernet: MultiFernet | None = (
                MultiFernet([Fernet(k.encode()) for k in parts]) if parts else None
            )
        except ValueError as exc:  # неверный формат ключа — ошибка конфигурации
            raise SecretBoxError("DATA_ENCRYPTION_KEY: ключ не в формате Fernet") from exc

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plain: str) -> str:
        if self._fernet is None:
            raise SecretBoxError("DATA_ENCRYPTION_KEY не задан")
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        if self._fernet is None:
            raise SecretBoxError("DATA_ENCRYPTION_KEY не задан")
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretBoxError("Секрет не расшифровывается: другой ключ или порча") from exc


_cache: tuple[str, SecretBox] | None = None


def secret_box() -> SecretBox:
    """Экземпляр по текущим настройкам; смена ключа (тесты, ротация) пересобирает его."""
    global _cache
    keys = settings.data_encryption_key
    if _cache is None or _cache[0] != keys:
        _cache = (keys, SecretBox(keys))
    return _cache[1]
