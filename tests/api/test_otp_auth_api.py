"""API-тесты OTP-входа и регистрации соло-врача.

Флоу: запрос кода → подтверждение → (регистрация анкеты | вход). Коды читаются
прямо из fakeredis — канал доставки заглушён.

Ключевые риски:

* соло-врач регистрируется по OTP (passwordless) и получает 30-дневный триал;
* повторный вход соло — тоже по OTP, без пароля;
* номер сотрудника клиники (с паролем) по OTP не логинится (409);
* билет регистрации одноразовый и обязателен; чужой телефон подставить нельзя.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from httpx import AsyncClient

from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070097{next(_phone_seq):04d}"


async def _code(res: Resources, phone: str) -> str:
    """Прочитать выпущенный OTP-код из fakeredis (доставка заглушена)."""
    code: str | None = await res.fake.get(f"auth:otp:code:{normalize_phone(phone)}")
    assert code is not None, "код не выпущен"
    return code


async def _refs(client: AsyncClient) -> tuple[str, str]:
    """(specializationId, cityId) из публичных справочников."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    city = (await client.get(f"{API}/cities")).json()[0]["id"]
    return spec, city


async def _register_solo(
    client: AsyncClient, res: Resources, phone: str, name: str
) -> dict[str, Any]:
    """Пройти весь флоу первичной регистрации соло-врача; вернуть тело входа."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verify = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await _code(res, phone)}
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["status"] == "registration_required"
    token = verify.json()["registrationToken"]

    spec, city = await _refs(client)
    registered = await client.post(
        f"{API}/auth/solo/register",
        json={
            "registrationToken": token,
            "fullName": name,
            "specializationId": spec,
            "cityId": city,
        },
    )
    assert registered.status_code == 201, registered.text
    body: dict[str, Any] = registered.json()
    return body


# ── 1. Первичная регистрация соло-врача ─────────────────────────────────────


async def test_solo_first_time_registration(
    client: AsyncClient, _resources: Resources
) -> None:
    """Полный путь: код → подтверждение → анкета → вход с 30-дневным триалом."""
    phone = _phone()
    body = await _register_solo(client, _resources, phone, "Соло Ортопед")

    assert body["accessToken"]
    assert body["account"]["phone"] == normalize_phone(phone)
    assert body["mustChangePassword"] is False  # passwordless — смены пароля нет
    assert len(body["clinics"]) == 1  # своя соло-клиника
    assert body["activeClinicId"] is not None

    headers = {"Authorization": f"Bearer {body['accessToken']}"}
    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["activeClinic"]["isOwner"] is True


async def test_solo_returning_login_by_otp(
    client: AsyncClient, _resources: Resources
) -> None:
    """Повторный вход соло-врача — снова по OTP, сразу authenticated."""
    phone = _phone()
    await _register_solo(client, _resources, phone, "Соло Терапевт")

    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    verify = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await _code(_resources, phone)}
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["status"] == "authenticated"
    assert verify.json()["auth"]["accessToken"]
    assert verify.json()["registrationToken"] is None


# ── 2. Проверки кода ────────────────────────────────────────────────────────


async def test_verify_wrong_code_is_400(client: AsyncClient, _resources: Resources) -> None:
    """Неверный код → 400 invalid_otp."""
    phone = _phone()
    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    resp = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": "000000"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_otp"


async def test_verify_without_requested_code_is_400(client: AsyncClient) -> None:
    """Подтверждение без запроса кода → 400."""
    resp = await client.post(
        f"{API}/auth/otp/verify", json={"phone": _phone(), "code": "123456"}
    )
    assert resp.status_code == 400, resp.text


# ── 3. Регистрация: билет и валидация ───────────────────────────────────────


async def test_register_without_valid_ticket_is_400(client: AsyncClient) -> None:
    """Регистрация с несуществующим билетом → 400."""
    spec, city = await _refs(client)
    resp = await client.post(
        f"{API}/auth/solo/register",
        json={
            "registrationToken": "bogus-token",
            "fullName": "Никто",
            "specializationId": spec,
            "cityId": city,
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "registration_not_verified"


async def test_registration_ticket_is_single_use(
    client: AsyncClient, _resources: Resources
) -> None:
    """Билет одноразовый: повторная регистрация тем же токеном → 400."""
    phone = _phone()
    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    verify = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await _code(_resources, phone)}
    )
    token = verify.json()["registrationToken"]
    spec, city = await _refs(client)
    payload = {
        "registrationToken": token,
        "fullName": "Соло Хирург",
        "specializationId": spec,
        "cityId": city,
    }
    first = await client.post(f"{API}/auth/solo/register", json=payload)
    assert first.status_code == 201, first.text
    second = await client.post(f"{API}/auth/solo/register", json=payload)
    assert second.status_code == 400, second.text


async def test_register_unknown_specialization_is_422(
    client: AsyncClient, _resources: Resources
) -> None:
    """Неизвестная специализация → 422; билет не сгорает (можно повторить)."""
    phone = _phone()
    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    verify = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await _code(_resources, phone)}
    )
    token = verify.json()["registrationToken"]
    _, city = await _refs(client)

    bad = await client.post(
        f"{API}/auth/solo/register",
        json={
            "registrationToken": token,
            "fullName": "Соло",
            "specializationId": "00000000-0000-4000-8000-000000000000",
            "cityId": city,
        },
    )
    assert bad.status_code == 422, bad.text

    # Билет уцелел — повтор с корректной специализацией проходит.
    spec, _ = await _refs(client)
    good = await client.post(
        f"{API}/auth/solo/register",
        json={
            "registrationToken": token,
            "fullName": "Соло",
            "specializationId": spec,
            "cityId": city,
        },
    )
    assert good.status_code == 201, good.text


# ── 4. Разделение с паролевым входом ────────────────────────────────────────


async def test_clinic_owner_phone_cannot_login_by_otp(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Номер владельца клиники (с паролем) по OTP не логинится → 409 use_password_login."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Клиники",
            "adminPhone": phone,
            "name": "Клиника OTP",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text

    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    verify = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await _code(_resources, phone)}
    )
    assert verify.status_code == 409, verify.text
    assert verify.json()["error"] == "use_password_login"


@pytest.mark.parametrize("path", ["/auth/otp/request", "/auth/otp/verify", "/auth/solo/register"])
async def test_otp_endpoints_are_public(client: AsyncClient, path: str) -> None:
    """OTP-ручки доступны без токена (это вход) — на пустое тело отвечают 422, не 401."""
    resp = await client.post(f"{API}{path}", json={})
    assert resp.status_code == 422, resp.text
