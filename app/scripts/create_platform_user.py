"""Создать/обновить платформенного админа Skrezo (единый вход).

Команда Skrezo теперь — обычный `account` с флагом `is_platform_admin` и входом
по телефону + паролю (единый контур). Скрипт создаёт такой аккаунт или обновляет
пароль/ФИО существующего.

Запуск:
    uv run python -m app.scripts.create_platform_user \
        --phone 0700000001 --password secret --full-name "Иван"
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.db import engine, session_factory
from app.core.security import hash_password
from app.shared.db.enums import Lang
from app.shared.db.models import Account
from app.shared.domain.value_objects import normalize_phone


async def create_or_update(phone_raw: str, password: str, full_name: str | None) -> str:
    phone = normalize_phone(phone_raw)
    async with session_factory() as session:
        account = (
            await session.execute(select(Account).where(Account.phone == phone))
        ).scalar_one_or_none()
        if account is None:
            session.add(
                Account(
                    phone=phone,
                    full_name=full_name or "Skrezo Admin",
                    lang=Lang.RU,
                    onboarded=True,
                    password_hash=hash_password(password),
                    must_change_password=False,
                    is_platform_admin=True,
                )
            )
            action = "создан"
        else:
            account.password_hash = hash_password(password)
            account.must_change_password = False
            account.is_platform_admin = True
            if full_name is not None:
                account.full_name = full_name
            action = "обновлён"
        await session.commit()
    await engine.dispose()
    return action


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать/обновить админа Skrezo")
    parser.add_argument("--phone", required=True, help="Телефон (= логин)")
    parser.add_argument("--password", required=True)
    parser.add_argument("--full-name", default=None)
    args = parser.parse_args()

    action = asyncio.run(create_or_update(args.phone, args.password, args.full_name))
    print(f"Админ Skrezo '{args.phone}' {action}.")


if __name__ == "__main__":
    main()
