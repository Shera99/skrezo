"""Создать тестовую соло-клинику с владельцем (для проверки клиентского входа).

Превью логики провижининга (фаза 3): создаёт клинику, клонирует глобальные
шаблоны ролей в роли клиники и заводит аккаунт-владельца.

    uv run python -m app.scripts.create_test_clinic --phone 0700123456 \
        --full-name "Иван Соло" --password secret123

Владелец создаётся с готовым паролем (``must_change_password=false``), чтобы им
можно было сразу войти через ``POST /auth/login``.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.db import engine, session_factory
from app.core.security import hash_password
from app.shared.db.enums import ClinicType, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.city import City
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission
from app.shared.domain.value_objects import normalize_phone


async def run(phone_raw: str, full_name: str, clinic_name: str, password: str) -> None:
    phone = normalize_phone(phone_raw)
    async with session_factory() as session:
        exists = (
            await session.execute(select(Account).where(Account.phone == phone))
        ).scalar_one_or_none()
        if exists is not None:
            print(f"Аккаунт {phone} уже существует — пропуск.")
            await engine.dispose()
            return

        city = (await session.execute(select(City).limit(1))).scalar_one()
        clinic = Clinic(name=clinic_name, type=ClinicType.SOLO, city_id=city.id, lang=Lang.RU)
        session.add(clinic)
        await session.flush()

        # Клонируем глобальные шаблоны ролей в роли этой клиники.
        templates = (
            (
                await session.execute(
                    select(Role).where(Role.clinic_id.is_(None), Role.is_system.is_(True))
                )
            )
            .scalars()
            .all()
        )
        owner_role: Role | None = None
        for tpl in templates:
            role = Role(
                clinic_id=clinic.id,
                key=tpl.key,
                name=tpl.name,
                is_system=True,
                is_owner=tpl.is_owner,
            )
            session.add(role)
            await session.flush()
            perm_ids = (
                (
                    await session.execute(
                        select(RolePermission.permission_id).where(RolePermission.role_id == tpl.id)
                    )
                )
                .scalars()
                .all()
            )
            for pid in perm_ids:
                session.add(RolePermission(role_id=role.id, permission_id=pid))
            if tpl.is_owner:
                owner_role = role

        if owner_role is None:
            raise RuntimeError("Не найден шаблон роли владельца — сначала прогоните сид (0004).")

        account = Account(
            phone=phone,
            full_name=full_name,
            lang=Lang.RU,
            onboarded=True,
            password_hash=hash_password(password),
            must_change_password=False,
        )
        session.add(account)
        await session.flush()
        session.add(
            Membership(
                clinic_id=clinic.id,
                account_id=account.id,
                role_id=owner_role.id,
                active=True,
            )
        )
        await session.commit()
        print(
            f"Клиника {clinic.id}, аккаунт {account.id} ({phone}), роль владельца {owner_role.id}."
        )

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать тестовую соло-клинику")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--full-name", default="Тестовый Врач")
    parser.add_argument("--clinic-name", default="Тестовая клиника")
    parser.add_argument("--password", default="secret123", help="Пароль владельца для входа")
    args = parser.parse_args()
    asyncio.run(run(args.phone, args.full_name, args.clinic_name, args.password))


if __name__ == "__main__":
    main()
