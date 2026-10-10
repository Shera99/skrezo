"""API-тесты POST /onboarding/complete — «мастер первичной настройки пройден».

Мобильное приложение зовёт ручку по завершении мастера, а при входе берёт флаг
``account.onboarded`` с сервера. Без ручки флаг выставить нечем, и после
повторного входа (или входа с другого телефона) мастер начинается заново.

Ключевые риски:

* приложение молча глотает ошибки ручки — значит, она не должна отвечать 422 или
  4xx там, где человек просто прошёл мастер; тело запроса не нужно вовсе
  (пустое, ``{}`` и даже «мусорное» равнозначны);
* ручка идемпотентна: повтор после обрыва связи или двойное нажатие безопасны и
  ничего не пишут в БД;
* флаг лежит на **аккаунте**: завершение мастера одним человеком не трогает
  коллег по клинике, зато общий для всех клиник самого человека;
* путь зашит в выпущенные сборки приложения и лежит НЕ под ``/auth``;
* авторизация и блокировка — как у остальных клиентских ручек.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from structlog.testing import capture_logs

from app.core.security import TokenType, create_token, hash_password
from app.shared.db.enums import Lang
from app.shared.db.models import Account
from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources, otp_code, otp_login

COMPLETE_URL = f"{API}/onboarding/complete"

_phone_seq = itertools.count(1)

#: Ключи объекта аккаунта (AccountOut) — единый для /auth/me и ответа ручки.
_ACCOUNT_KEYS = {"id", "fullName", "phone", "lang", "onboarded", "blocked"}


def _phone() -> str:
    return f"070097{next(_phone_seq):04d}"


# ── Помощники ───────────────────────────────────────────────────────────────


async def _provision_clinic(admin_client: AsyncClient, owner_phone: str) -> str:
    """Завести клинику через админку; владелец создаётся с onboarded=false."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Мастера",
            "adminPhone": owner_phone,
            "name": f"Клиника {owner_phone}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _add_doctor(admin_client: AsyncClient, clinic_id: str, phone: str) -> None:
    """Добавить врача в клинику (аккаунт врача тоже заводится с onboarded=false)."""
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    added = await admin_client.post(
        f"{API}/admin/clients/{clinic_id}/doctors",
        json={"fullName": "Врач Коллега", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text


async def _clinic_with_owner_and_doctor(
    admin_client: AsyncClient, client: AsyncClient
) -> tuple[dict[str, str], dict[str, str], str, str]:
    """Клиника + владелец + врач.

    Вернуть (заголовки владельца, заголовки врача, телефон владельца, телефон врача).
    """
    owner_phone, doctor_phone = _phone(), _phone()
    clinic_id = await _provision_clinic(admin_client, owner_phone)
    await _add_doctor(admin_client, clinic_id, doctor_phone)
    owner = await otp_login(client, owner_phone)
    doctor = await otp_login(client, doctor_phone)
    return owner, doctor, owner_phone, doctor_phone


async def _me_account(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    account: dict[str, Any] = me.json()["account"]
    return account


async def _login_auth(client: AsyncClient, phone: str) -> dict[str, Any]:
    """Войти по OTP и вернуть объект ``auth`` из ответа входа (токены + аккаунт)."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["status"] == "authenticated", body
    auth: dict[str, Any] = body["auth"]
    return auth


async def _db_onboarded(res: Resources, phone: str) -> bool:
    """Значение флага прямо в БД — независимо от того, что ответил API."""
    async with res.factory() as session:
        value = (
            await session.execute(
                text("SELECT onboarded FROM account WHERE phone = :phone"),
                {"phone": normalize_phone(phone)},
            )
        ).scalar_one()
        return bool(value)


async def _db_xmin(res: Resources, phone: str) -> str:
    """Системный ``xmin`` строки аккаунта: меняется при любой записи (UPDATE)."""
    async with res.factory() as session:
        return str(
            (
                await session.execute(
                    text("SELECT xmin::text FROM account WHERE phone = :phone"),
                    {"phone": normalize_phone(phone)},
                )
            ).scalar_one()
        )


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


# ── Авторизация ─────────────────────────────────────────────────────────────


async def test_complete_without_token_returns_401(client: AsyncClient) -> None:
    """Без заголовка Authorization ручка закрыта."""
    resp = await client.post(COMPLETE_URL)
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"] == "unauthorized"


async def test_complete_with_garbage_token_returns_401_invalid_token(
    client: AsyncClient,
) -> None:
    """Токен-мусор → 401 invalid_token, а не 500 и не 200."""
    resp = await client.post(COMPLETE_URL, headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"] == "invalid_token"


async def test_complete_with_refresh_token_returns_401_invalid_token(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Refresh-токеном ручку не открыть: нужен именно access."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    await client.post(f"{API}/auth/otp/request", json={"phone": owner_phone})
    verified = await client.post(
        f"{API}/auth/otp/verify",
        json={"phone": owner_phone, "code": await otp_code(owner_phone)},
    )
    refresh_token = verified.json()["auth"]["refreshToken"]

    resp = await client.post(COMPLETE_URL, headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"] == "invalid_token"


async def test_complete_is_not_available_under_auth_prefix(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Путь зашит в сборки приложения: ровно ``/onboarding/complete``, не ``/auth/...``."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    wrong = await client.post(f"{API}/auth/onboarding/complete", headers=owner)
    assert wrong.status_code == 404, wrong.text
    # И неверный метод по верному пути — 405, а не тихий 200.
    assert (await client.get(COMPLETE_URL, headers=owner)).status_code == 405
    # Чужие пути ничего не выставили.
    assert (await _me_account(client, owner))["onboarded"] is False


# ── Основной сценарий ───────────────────────────────────────────────────────


async def test_owner_completes_onboarding_without_body_flag_goes_true(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Новый владелец: onboarded=false → POST без тела → 200, true → /auth/me тоже true."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    assert (await _me_account(client, owner))["onboarded"] is False
    assert await _db_onboarded(_resources, owner_phone) is False

    resp = await client.post(COMPLETE_URL, headers=owner)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["onboarded"] is True
    assert body["phone"] == normalize_phone(owner_phone)
    assert body["blocked"] is False
    assert (await _me_account(client, owner))["onboarded"] is True
    assert await _db_onboarded(_resources, owner_phone) is True


async def test_complete_accepts_empty_json_object(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Приложение может прислать ``{}`` — это тоже 200."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    resp = await client.post(COMPLETE_URL, headers=owner, json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["onboarded"] is True


async def test_complete_accepts_empty_content_with_json_header(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Пустое тело с ``Content-Type: application/json`` не должно давать 422."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    resp = await client.post(
        COMPLETE_URL, headers={**owner, "Content-Type": "application/json"}, content=b""
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["onboarded"] is True


async def test_complete_ignores_body_content_and_cannot_reset_flag(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Содержимое тела игнорируется: ``onboarded=false`` в нём флаг не сбросит и 422 не даст."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    first = await client.post(
        COMPLETE_URL, headers=owner, json={"onboarded": False, "fullName": "Подмена", "x": [1]}
    )
    assert first.status_code == 200, first.text
    assert first.json()["onboarded"] is True
    # Лишние поля тела не просочились в аккаунт.
    assert first.json()["fullName"] == "Владелец Мастера"

    # Обратного сброса через клиентскую ручку нет — и тело его не заменит.
    second = await client.post(COMPLETE_URL, headers=owner, json={"onboarded": False})
    assert second.status_code == 200, second.text
    assert second.json()["onboarded"] is True
    assert await _db_onboarded(_resources, owner_phone) is True


# ── Идемпотентность ─────────────────────────────────────────────────────────


async def test_complete_twice_is_idempotent(admin_client: AsyncClient, client: AsyncClient) -> None:
    """Повторный вызов — снова 200 с тем же телом, без ошибок."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    first = await client.post(COMPLETE_URL, headers=owner)
    second = await client.post(COMPLETE_URL, headers=owner)
    third = await client.post(COMPLETE_URL, headers=owner, json={})

    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json() == second.json() == third.json()
    assert second.json()["onboarded"] is True


async def test_repeated_complete_does_not_write_to_db(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Повтор ничего не пишет: версия строки (xmin) не меняется, а лог — один."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)
    xmin_before = await _db_xmin(_resources, owner_phone)

    with capture_logs() as logs:
        first = await client.post(COMPLETE_URL, headers=owner)
    assert first.status_code == 200, first.text
    xmin_after_first = await _db_xmin(_resources, owner_phone)
    # Первый вызов — настоящая запись: версия строки сменилась.
    assert xmin_after_first != xmin_before

    with capture_logs() as repeat_logs:
        second = await client.post(COMPLETE_URL, headers=owner)
    assert second.status_code == 200, second.text

    assert await _db_xmin(_resources, owner_phone) == xmin_after_first
    onboarded_events = [e for e in logs if e["event"] == "account_onboarded"]
    assert len(onboarded_events) == 1
    assert onboarded_events[0]["account_id"] == first.json()["id"]
    assert onboarded_events[0]["log_level"] == "info"
    # Событие пишется только при первом переходе false → true.
    assert [e for e in repeat_logs if e["event"] == "account_onboarded"] == []


async def test_parallel_complete_calls_all_succeed(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Двойное нажатие: параллельные вызовы не конфликтуют, итог — true."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    responses = await asyncio.gather(*(client.post(COMPLETE_URL, headers=owner) for _ in range(4)))

    assert [r.status_code for r in responses] == [200] * 4, [r.text for r in responses]
    assert all(r.json()["onboarded"] is True for r in responses)
    assert await _db_onboarded(_resources, owner_phone) is True


# ── Повторный вход: флаг берётся с сервера ──────────────────────────────────


async def test_relogin_returns_onboarded_true_after_complete(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Ради этого ручка и нужна: новый вход (другой телефон) видит onboarded=true."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)

    first_login = await _login_auth(client, owner_phone)
    assert first_login["account"]["onboarded"] is False  # без ручки мастер начался бы здесь

    headers = {"Authorization": f"Bearer {first_login['accessToken']}"}
    done = await client.post(COMPLETE_URL, headers=headers)
    assert done.status_code == 200, done.text

    # Новый вход (как с другого телефона): ни токены, ни локальный флаг не нужны.
    second_login = await _login_auth(client, owner_phone)
    assert second_login["account"]["onboarded"] is True
    assert second_login["account"]["id"] == first_login["account"]["id"]

    relogged = {"Authorization": f"Bearer {second_login['accessToken']}"}
    assert (await _me_account(client, relogged))["onboarded"] is True


# ── Флаг на аккаунте, а не на клинике ───────────────────────────────────────


async def test_owner_completion_does_not_touch_colleague_doctor(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Врач той же клиники остаётся onboarded=false, пока сам не вызовет ручку."""
    owner, doctor, owner_phone, doctor_phone = await _clinic_with_owner_and_doctor(
        admin_client, client
    )
    assert (await _me_account(client, doctor))["onboarded"] is False

    done = await client.post(COMPLETE_URL, headers=owner)
    assert done.status_code == 200, done.text

    assert (await _me_account(client, owner))["onboarded"] is True
    assert (await _me_account(client, doctor))["onboarded"] is False
    assert await _db_onboarded(_resources, doctor_phone) is False

    # Врач проходит мастер сам — и только тогда у него true.
    doctor_done = await client.post(COMPLETE_URL, headers=doctor)
    assert doctor_done.status_code == 200, doctor_done.text
    assert doctor_done.json()["onboarded"] is True
    assert doctor_done.json()["phone"] == normalize_phone(doctor_phone)
    assert (await _me_account(client, doctor))["onboarded"] is True
    assert await _db_onboarded(_resources, owner_phone) is True


async def test_doctor_completion_does_not_touch_owner(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Обратное направление: врач прошёл мастер — владелец по-прежнему false."""
    owner, doctor, owner_phone, _ = await _clinic_with_owner_and_doctor(admin_client, client)

    done = await client.post(COMPLETE_URL, headers=doctor)
    assert done.status_code == 200, done.text

    assert (await _me_account(client, owner))["onboarded"] is False
    assert await _db_onboarded(_resources, owner_phone) is False


async def test_complete_works_without_clinic_in_token_and_is_shared_across_clinics(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Человек в двух клиниках: токен без клиники не мешает, а флаг один на обе."""
    clinic_a = await _provision_clinic(admin_client, _phone())
    clinic_b = await _provision_clinic(admin_client, _phone())
    doctor_phone = _phone()
    await _add_doctor(admin_client, clinic_a, doctor_phone)
    # Через API того же человека во вторую клинику не добавить (phone_already_used),
    # поэтому второе членство заводим прямо в БД.
    async with _resources.factory() as session:
        account_id = (
            await session.execute(
                text("SELECT id FROM account WHERE phone = :phone"),
                {"phone": normalize_phone(doctor_phone)},
            )
        ).scalar_one()
        role_id = (
            await session.execute(
                text("SELECT id FROM role WHERE clinic_id = :clinic AND key = 'worker'"),
                {"clinic": clinic_b},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO membership (id, clinic_id, account_id, role_id, active) "
                "VALUES (gen_random_uuid(), :clinic, :account, :role, true)"
            ),
            {"clinic": clinic_b, "account": account_id, "role": role_id},
        )
        await session.commit()

    requested = await client.post(f"{API}/auth/otp/request", json={"phone": doctor_phone})
    assert requested.status_code == 200, requested.text
    verified = await client.post(
        f"{API}/auth/otp/verify",
        json={"phone": doctor_phone, "code": await otp_code(doctor_phone)},
    )
    auth = verified.json()["auth"]
    assert len(auth["clinics"]) == 2
    assert auth["activeClinicId"] is None  # клиника в токене не выбрана
    headers = {"Authorization": f"Bearer {auth['accessToken']}"}

    done = await client.post(COMPLETE_URL, headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["onboarded"] is True

    # Выбор любой из клиник флаг не меняет: он на аккаунте.
    for clinic_id in (clinic_a, clinic_b):
        selected = await client.post(
            f"{API}/auth/select-clinic", headers=headers, json={"clinicId": clinic_id}
        )
        assert selected.status_code == 200, selected.text
        scoped = {"Authorization": f"Bearer {selected.json()['accessToken']}"}
        assert (await _me_account(client, scoped))["onboarded"] is True


async def test_platform_admin_can_complete_onboarding_without_clinic(
    client: AsyncClient, _resources: Resources
) -> None:
    """Админ Skrezo (клиники в токене нет вовсе) тоже получает флаг: он на аккаунте."""
    phone = _phone()
    async with _resources.factory() as session:
        session.add(
            Account(
                phone=normalize_phone(phone),
                full_name="Админ Мастера",
                lang=Lang.RU,
                onboarded=False,
                password_hash=hash_password("parol-admina-123"),
                must_change_password=False,
                is_platform_admin=True,
            )
        )
        await session.commit()
    login = await client.post(
        f"{API}/auth/login", json={"phone": phone, "password": "parol-admina-123"}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    resp = await client.post(COMPLETE_URL, headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["onboarded"] is True
    assert await _db_onboarded(_resources, phone) is True


# ── Блокировка и отсутствие аккаунта ────────────────────────────────────────


async def test_blocked_account_gets_403_and_flag_stays_false(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Заблокированный аккаунт → 403 account_blocked, флаг в БД не тронут."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    account_id = await _account_id(_resources, owner_phone)
    blocked = await admin_client.post(f"{API}/admin/accounts/{account_id}/block")
    assert blocked.status_code == 200, blocked.text

    resp = await client.post(COMPLETE_URL, headers=owner)

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "account_blocked"
    assert await _db_onboarded(_resources, owner_phone) is False


async def test_complete_for_unknown_account_returns_404_account_not_found(
    client: AsyncClient,
) -> None:
    """Подписанный access-токен несуществующего аккаунта → 404 account_not_found."""
    token = create_token(subject=str(uuid.uuid4()), token_type=TokenType.ACCESS)

    resp = await client.post(COMPLETE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "account_not_found"


# ── Форма ответа ────────────────────────────────────────────────────────────


async def test_complete_response_has_same_keys_as_auth_me_account(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Ответ — тот же объект, что ``account`` в /auth/me: ключи camelCase и значения совпадают."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)

    resp = await client.post(COMPLETE_URL, headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    me_account = await _me_account(client, owner)
    assert set(body) == set(me_account) == _ACCOUNT_KEYS
    assert body == me_account
    uuid.UUID(body["id"])  # id — валидный UUID


async def test_complete_response_account_matches_login_response(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Объект аккаунта в ответе ручки и в ответе входа — одной формы."""
    owner_phone = _phone()
    await _provision_clinic(admin_client, owner_phone)
    owner = await otp_login(client, owner_phone)
    body = (await client.post(COMPLETE_URL, headers=owner)).json()

    login_account = (await _login_auth(client, owner_phone))["account"]

    assert set(body) == set(login_account)
    assert body == login_account


# ── Документация (OpenAPI) ──────────────────────────────────────────────────


async def test_openapi_documents_endpoint_without_request_body(client: AsyncClient) -> None:
    """В схеме: POST без requestBody, ответ AccountOut и коды 401/403/404 с примерами."""
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][COMPLETE_URL]["post"]

    assert set(schema["paths"][COMPLETE_URL]) == {"post"}
    assert "requestBody" not in operation  # тело не нужно: пустое и {} равнозначны
    assert operation["tags"] == ["auth"]
    assert operation["summary"]
    assert operation["responses"]["200"]["description"]
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/AccountOut"
    )
    documented_errors = {
        "401": {"unauthorized", "invalid_token"},
        "403": {"account_blocked"},
        "404": {"account_not_found"},
    }
    for code, errors in documented_errors.items():
        media = operation["responses"][code]["content"]["application/json"]
        if "examples" in media:
            found = {ex["value"]["error"] for ex in media["examples"].values()}
        else:
            found = {media["example"]["error"]}
        assert found == errors, code

    onboarded = schema["components"]["schemas"]["AccountOut"]["properties"]["onboarded"]
    assert onboarded["description"]
