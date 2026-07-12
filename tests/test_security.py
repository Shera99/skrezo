"""Юнит-тесты криптопримитивов: пароли и JWT двух контуров (без БД/Redis)."""

from __future__ import annotations

import jwt
import pytest

from app.core.security import (
    TokenType,
    create_token,
    decode_token,
    generate_temp_password,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert verify_password("secret123", hashed)
    assert not verify_password("wrong", hashed)


def test_generate_temp_password() -> None:
    pwd = generate_temp_password()
    assert len(pwd) == 10
    # Заданная длина уважается и пароли не повторяются (криптослучайность).
    assert len(generate_temp_password(16)) == 16
    assert generate_temp_password() != generate_temp_password()
    # Без похожих символов (0/O/1/l/I) — чтобы диктовать вслух.
    assert not (set(pwd) & set("0O1lI"))


def test_token_roundtrip() -> None:
    token = create_token(
        subject="user-1",
        token_type=TokenType.ACCESS,
        extra_claims={"platform_admin": True},
    )
    payload = decode_token(token)
    assert payload["sub"] == "user-1"
    assert payload["type"] == "access"
    assert payload["aud"] == "skrezo"
    assert payload["platform_admin"] is True


def test_tampered_token_is_rejected() -> None:
    # Подделка подписи (единый контур, один секрет/aud) — токен невалиден.
    token = create_token(subject="user-1", token_type=TokenType.ACCESS)
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered)
