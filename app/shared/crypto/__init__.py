"""Шифрование секретов, которые приходится хранить в базе (см. ``secret_box``)."""

from __future__ import annotations

from app.shared.crypto.secret_box import SecretBox, SecretBoxError, secret_box

__all__ = ["SecretBox", "SecretBoxError", "secret_box"]
