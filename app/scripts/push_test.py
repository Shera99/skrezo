"""Тестовый пуш на все устройства номера — проверка доставки FCM/APNs.

Шлёт пуш напрямую через транспорт, в обход событий, тихих часов и ленты
уведомлений: проверяется только цепочка «наши ключи → провайдер → телефон».
По каждому устройству печатает ответ провайдера и подсказку, что чинить.

    make push-test phone=0700123456
    uv run python -m app.scripts.push_test --phone 0700123456 --body "Привет"

На сервере (без make):

    sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && \\
        .venv/bin/python -m app.scripts.push_test --phone 0700123456'

Ничего не меняет в базе: мёртвый токен только показывается (в бою его снимет
диспетчер при первой же настоящей рассылке), строка телеметрии не пишется.
Код выхода 0 — хотя бы одно устройство принял провайдер, иначе 1.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.db import engine, session_factory
from app.core.errors import ValidationAppError
from app.shared.db.enums import DevicePlatform
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.device import Device
from app.shared.domain.value_objects import normalize_phone
from app.shared.notifications.team_format import team_datetime
from app.shared.push.channels import DeadTokenError, PushDeliveryError
from app.shared.push.factory import build_push_transport
from app.shared.push.message import PushMessage
from app.shared.push.transport import PushTransport

# Канал Android, который приложение создаёт первым делом: пуш в несозданный
# канал Android 8+ молча уводит в «Прочее» — проверка тогда врала бы о звуке.
_ANDROID_CHANNEL = "schedule-changes.v1"
_PROVIDER = {"fcm": "FCM", "apns": "APNs"}


@dataclass(frozen=True, slots=True)
class _Target:
    """Устройство адресата — то, что нужно для отправки и для отчёта."""

    platform: DevicePlatform
    token: str
    app_version: str | None
    clinic_name: str
    last_seen_at: datetime | None


def _message(title: str | None, body: str | None) -> PushMessage:
    now = team_datetime(datetime.now(UTC))
    return PushMessage(
        # Тип «test» приложение не знает — по тапу просто откроется главный экран.
        data={"type": "test", "id": f"test_{uuid.uuid4().hex[:12]}"},
        title=title or "Проверка уведомлений Skrezo",
        body=body or f"Тестовый пуш, {now}. Видите его — всё работает ✅",
        collapse_key="push-test",
        urgent=True,
        android_channel=_ANDROID_CHANNEL,
        # Десять минут: телефон, включённый через час, старый тест не покажет.
        ttl_seconds=600,
    )


def _dead_token_hint(platform: DevicePlatform) -> str:
    if platform is DevicePlatform.IOS:
        return (
            "токен недействителен. Для iOS чаще всего это несовпадение среды APNs: "
            "TestFlight и App Store → APNS_USE_SANDBOX=false, сборка из Xcode → true. "
            "Иначе — приложение удалено или переустановлено: войти в него заново."
        )
    return "токен недействителен: приложение удалено или переустановлено — войти в него заново."


async def _targets(phone: str) -> tuple[Account | None, list[_Target]]:
    async with session_factory() as session:
        account = (
            await session.execute(select(Account).where(Account.phone == phone))
        ).scalar_one_or_none()
        if account is None:
            return None, []
        rows = (
            await session.execute(
                select(
                    Device.platform,
                    Device.token,
                    Device.app_version,
                    Clinic.name,
                    Device.last_seen_at,
                )
                .join(Clinic, Clinic.id == Device.clinic_id)
                .where(Device.account_id == account.id)
                .order_by(Device.last_seen_at.desc().nulls_last())
            )
        ).all()
    return account, [_Target(*row) for row in rows]


async def run(
    phone_raw: str,
    *,
    title: str | None = None,
    body: str | None = None,
    transport: PushTransport | None = None,
) -> int:
    """Разослать тестовый пуш и напечатать отчёт; вернуть код выхода."""
    try:
        phone = normalize_phone(phone_raw)
    except ValidationAppError:
        print(f"❌ Некорректный номер: {phone_raw}")
        return 1

    account, targets = await _targets(phone)
    if account is None:
        print(f"❌ Аккаунта с номером {phone} нет.")
        return 1

    transport = transport or build_push_transport()
    print(f"Аккаунт: {account.full_name} ({phone})")
    print(
        f"PUSH_ENABLED={str(settings.push_enabled).lower()} · "
        f"android → {transport.channel_name(DevicePlatform.ANDROID)} · "
        f"ios → {transport.channel_name(DevicePlatform.IOS)}"
    )
    if not targets:
        print(
            "❌ У аккаунта нет зарегистрированных устройств. Войти в приложение на телефоне "
            "и разрешить уведомления — приложение само пришлёт токен (POST /clinic/devices)."
        )
        return 1

    message = _message(title, body)
    accepted = 0
    print(f"Устройств: {len(targets)}\n")
    for number, target in enumerate(targets, start=1):
        seen = team_datetime(target.last_seen_at) if target.last_seen_at else "—"
        print(
            f"{number}. {target.platform.value} · версия {target.app_version or '—'} · "
            f"«{target.clinic_name}» · последний вход {seen} · токен {target.token[:10]}…"
        )
        channel = transport.channel_name(target.platform)
        if channel == "log":
            # Лог-фолбэк «доставляет» молча — без этой проверки отчёт соврал бы ✅.
            print(
                "   ⚠️ живой канал не настроен — пуш ушёл только в лог. Нужны PUSH_ENABLED=true "
                "и ключи " + ("FCM" if target.platform is DevicePlatform.ANDROID else "APNs")
            )
            continue
        provider = _PROVIDER.get(channel, channel)
        try:
            await transport.send(target.platform, target.token, message)
        except DeadTokenError:
            print(f"   💀 {provider}: {_dead_token_hint(target.platform)}")
        except PushDeliveryError as exc:
            print(f"   ❌ {exc} — подробности в логе (fcm_send_failed / apns_send_failed)")
        except Exception as exc:  # ключ, сеть, OAuth — для диагностики важен любой отказ
            print(f"   ❌ {provider}: {type(exc).__name__}: {exc}")
        else:
            accepted += 1
            print(f"   ✅ {provider} принял пуш — проверьте телефон (приложение свернуть)")

    print()
    if accepted:
        print(f"Принято провайдером: {accepted} из {len(targets)}.")
        return 0
    print("Ни одно устройство не приняло пуш.")
    return 1


async def _main(args: argparse.Namespace) -> int:
    try:
        return await run(args.phone, title=args.title, body=args.body)
    except (OSError, SQLAlchemyError) as exc:
        # Не та машина или не тот .env — трассировка тут ничего не объясняет.
        print(f"❌ База недоступна ({type(exc).__name__}): проверьте DATABASE_URL в .env — {exc}")
        return 1
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Тестовый пуш на все устройства номера")
    parser.add_argument("--phone", required=True, help="номер аккаунта в любом формате")
    parser.add_argument("--title", help="заголовок пуша (по умолчанию — «Проверка уведомлений»)")
    parser.add_argument("--body", help="текст пуша")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
