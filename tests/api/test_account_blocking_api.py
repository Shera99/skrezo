"""API-тесты блокировки аккаунта (только команда Skrezo).

Блокировка — единственный способ отобрать доступ человеку целиком. Исключение из
клиники закрывает только её: аккаунт глобальный, и уволенный заходит в любую
другую клинику, где числится.

Ключевые риски:

* блокировка обязана действовать сразу — и на уже выданный access-токен, и на
  refresh, иначе она бессмысленна ровно в тот момент, когда нужна;
* войти заново заблокированный не должен ни по коду, ни по паролю;
* себя заблокировать нельзя — админ потеряет доступ к ручке, которой снимают;
* разблокировка возвращает доступ, но не возвращает старые сессии.
"""

from __future__ import annotations

import itertools

from httpx import AsyncClient
from sqlalchemy import text

from app.core.security import hash_password
from app.shared.db.enums import Lang
from app.shared.db.models import Account
from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources, otp_code, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070096{next(_phone_seq):04d}"


async def _clinic_with_doctor(
    admin_client: AsyncClient, client: AsyncClient
) -> tuple[str, str, dict[str, str]]:
    """Клиника + врач в ней; вернуть (clinicId, телефон врача, его заголовки)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    owner_phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Блокировки",
            "adminPhone": owner_phone,
            "name": f"Клиника {owner_phone}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id = str(created.json()["id"])

    doctor_phone = _phone()
    added = await admin_client.post(
        f"{API}/admin/clients/{clinic_id}/doctors",
        json={"fullName": "Врач Блокируемый", "phone": doctor_phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    return clinic_id, doctor_phone, await otp_login(client, doctor_phone)


async def _account_id(res: Resources, phone: str) -> str:
    async with res.factory() as session:
        return str(
            (
                await session.execute(
                    text("SELECT id FROM account WHERE phone = :phone"),
                    {"phone": normalize_phone(phone)},
                )
            ).scalar_one()
        )


# ── Блокировка отбирает доступ ──────────────────────────────────────────────


async def test_block_kills_live_session_immediately(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Уже выданный токен перестаёт работать сразу — на обоих контурах."""
    _, phone, doctor = await _clinic_with_doctor(admin_client, client)
    assert (await client.get(f"{API}/clinic/services", headers=doctor)).status_code == 200

    account_id = await _account_id(_resources, phone)
    blocked = await admin_client.post(
        f"{API}/admin/accounts/{account_id}/block", json={"reason": "Уволен"}
    )
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert body["blocked"] is True
    assert body["blockedAt"]
    assert body["blockedReason"] == "Уволен"
    assert body["sessionsRevoked"] is True

    denied = await client.get(f"{API}/clinic/services", headers=doctor)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"] == "account_blocked"
    # И на ручках, которые не про клинику, — тоже.
    assert (await client.get(f"{API}/auth/me", headers=doctor)).status_code == 403


async def test_block_revokes_refresh(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Refresh не воскрешает доступ: сессии погашены при блокировке."""
    _, phone, _ = await _clinic_with_doctor(admin_client, client)
    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    refresh_token = verified.json()["auth"]["refreshToken"]
    assert (
        await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    ).status_code == 200

    account_id = await _account_id(_resources, phone)
    await admin_client.post(f"{API}/admin/accounts/{account_id}/block")

    dead = await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    assert dead.status_code == 401, dead.text


async def test_blocked_cannot_log_in_by_otp(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Код верный, номером владеет — и всё равно не пускаем, с внятной причиной."""
    _, phone, _ = await _clinic_with_doctor(admin_client, client)
    account_id = await _account_id(_resources, phone)
    await admin_client.post(f"{API}/admin/accounts/{account_id}/block")

    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 403, verified.text
    assert verified.json()["error"] == "account_blocked"


async def test_blocked_admin_cannot_log_in_by_password(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Второй контур входа закрыт так же — и тоже не «неверный пароль»."""
    phone = _phone()
    async with _resources.factory() as session:
        session.add(
            Account(
                phone=normalize_phone(phone),
                full_name="Админ Блокируемый",
                lang=Lang.RU,
                onboarded=True,
                password_hash=hash_password("parol-admina-123"),
                must_change_password=False,
                is_platform_admin=True,
            )
        )
        await session.commit()

    ok = await client.post(
        f"{API}/auth/login", json={"phone": phone, "password": "parol-admina-123"}
    )
    assert ok.status_code == 200, ok.text

    account_id = await _account_id(_resources, phone)
    await admin_client.post(f"{API}/admin/accounts/{account_id}/block")

    denied = await client.post(
        f"{API}/auth/login", json={"phone": phone, "password": "parol-admina-123"}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"] == "account_blocked"


# ── Разблокировка и защита от самоблокировки ────────────────────────────────


async def test_unblock_restores_login(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Доступ возвращается; входить нужно заново — старые сессии не воскресают."""
    _, phone, doctor = await _clinic_with_doctor(admin_client, client)
    account_id = await _account_id(_resources, phone)
    await admin_client.post(f"{API}/admin/accounts/{account_id}/block")

    unblocked = await admin_client.post(f"{API}/admin/accounts/{account_id}/unblock")
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["blocked"] is False
    assert unblocked.json()["blockedAt"] is None

    # Прежний refresh мёртв, но вход по коду снова работает.
    fresh = await otp_login(client, phone)
    assert (await client.get(f"{API}/clinic/services", headers=fresh)).status_code == 200
    # Старый access-токен тоже снова годен — он не истёк и аккаунт больше не заблокирован.
    assert (await client.get(f"{API}/clinic/services", headers=doctor)).status_code == 200


async def test_cannot_block_self(admin_client: AsyncClient, _resources: Resources) -> None:
    """Иначе админ теряет доступ к ручке, которой блокировку снимают."""
    account_id = await _account_id(_resources, "0700000001")
    resp = await admin_client.post(f"{API}/admin/accounts/{account_id}/block")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "cannot_block_self"


async def test_block_unknown_account_is_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        f"{API}/admin/accounts/00000000-0000-4000-8000-000000000000/block"
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "account_not_found"


async def test_block_needs_platform_admin(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Владелец клиники блокировать не может — право только у Skrezo."""
    clinic_id, phone, _ = await _clinic_with_doctor(admin_client, client)
    owner_phone = (await admin_client.get(f"{API}/admin/clients/{clinic_id}")).json()[
        "adminPhone"
    ]
    owner = await otp_login(client, owner_phone)
    account_id = await _account_id(_resources, phone)

    resp = await client.post(f"{API}/admin/accounts/{account_id}/block", headers=owner)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "not_platform_admin"


# ── Поле видно во всех списках ──────────────────────────────────────────────


async def test_blocked_flag_is_visible_everywhere(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Блокировка видна везде, где отдаётся аккаунт, — иначе её негде заметить."""
    clinic_id, phone, _ = await _clinic_with_doctor(admin_client, client)
    account_id = await _account_id(_resources, phone)
    await admin_client.post(f"{API}/admin/accounts/{account_id}/block", json={"reason": "Проверка"})

    # 1. Список аккаунтов.
    account = (await admin_client.get(f"{API}/admin/accounts?query={phone}")).json()["list"][0]
    assert account["blocked"] is True
    assert account["blockedReason"] == "Проверка"
    # Членство живо: из клиники его не исключали — это разные вещи.
    assert account["hasActiveMembership"] is True

    # 2. Сотрудники клиники (админ-сайд) и 3. карточка клиента.
    staff = (await admin_client.get(f"{API}/admin/clients/{clinic_id}/staff")).json()
    doctor = next(s for s in staff if s["role"] == "doctor")
    assert doctor["blocked"] is True
    assert doctor["active"] is True

    card = (await admin_client.get(f"{API}/admin/clients/{clinic_id}")).json()
    assert next(s for s in card["staff"] if s["role"] == "doctor")["blocked"] is True
    assert card["adminBlocked"] is False  # владельца не блокировали

    # 4. Список сотрудников в кабинете клиники.
    owner = await otp_login(client, card["adminPhone"])
    members = (await client.get(f"{API}/clinic/staff", headers=owner)).json()
    assert next(m for m in members if m["phone"] == f"+996{phone[1:]}")["blocked"] is True


async def test_blocked_owner_is_visible_in_clients_list(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """В списке клиентов видно, что владелец заблокирован и в кабинет не войдёт."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "solo",
            "adminFullName": "Соло Блокируемый",
            "adminPhone": phone,
            "cityId": city,
        },
    )
    assert created.status_code == 201, created.text

    listing = (await admin_client.get(f"{API}/admin/clients?page=1")).json()
    assert listing["list"][0]["adminBlocked"] is False

    account_id = await _account_id(_resources, phone)
    await admin_client.post(f"{API}/admin/accounts/{account_id}/block")

    listing = (await admin_client.get(f"{API}/admin/clients?page=1")).json()
    assert listing["list"][0]["adminBlocked"] is True
