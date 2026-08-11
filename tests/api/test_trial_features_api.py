"""API-тесты триал-функций: ограничение прав на триале и админ-настройка.

Клиника на 30-дневном триале использует только права из настраиваемого админкой
списка (``permission.in_trial``). По умолчанию доступно всё — ограничение
включается, когда админ выключает функцию.

Ключевые риски:

* на триале выключенная функция → 403 ``trial_restricted`` (в т.ч. у владельца);
* ограничение покрывает и скоуп-guard'ы (расписание/касса/отчёты), не только
  общий ``require_permission``;
* платная клиника (не на триале) ограничению не подвержена;
* админ-настройка применяется сразу (кеш сбрасывается) и доступна только Skrezo.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070098{next(_phone_seq):04d}"


async def _provision_clinic(
    admin_client: AsyncClient, client: AsyncClient, name: str
) -> tuple[str, dict[str, str]]:
    """Клиника (стартует на триале) + вход владельцем. Вернуть (clinicId, headers)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": f"Владелец {name}",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)
    return str(created.json()["id"]), headers


async def _set_status(res: Resources, clinic_id: str, status: SubscriptionStatus) -> None:
    async with res.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(clinic_id))
            .values(status=status)
        )
        await session.commit()


async def _set_feature(admin_client: AsyncClient, key: str, in_trial: bool) -> None:
    resp = await admin_client.patch(
        f"{API}/admin/trial-features", json={"items": [{"key": key, "inTrial": in_trial}]}
    )
    assert resp.status_code == 200, resp.text


# ── 1. Ограничение на триале ────────────────────────────────────────────────


async def test_trial_restricts_disabled_feature_even_for_owner(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Выключенная на триале функция закрыта даже владельцу; включённая — открыта."""
    _, headers = await _provision_clinic(admin_client, client, "Триал Патиенты")

    # По умолчанию всё в триале — patients.view доступен.
    ok = await client.get(f"{API}/clinic/patients", headers=headers)
    assert ok.status_code == 200, ok.text

    # Админ выключает patients.view на триале → 403 trial_restricted.
    await _set_feature(admin_client, "patients.view", False)
    blocked = await client.get(f"{API}/clinic/patients", headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"] == "trial_restricted"

    # Возвращаем — снова доступно (кеш сбрасывается на PATCH).
    await _set_feature(admin_client, "patients.view", True)
    restored = await client.get(f"{API}/clinic/patients", headers=headers)
    assert restored.status_code == 200, restored.text


async def test_trial_restriction_covers_scope_guards(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Ограничение работает и для скоуп-guard'ов (расписание), не только require_permission."""
    _, headers = await _provision_clinic(admin_client, client, "Триал Расписание")
    day = "2026-07-20"

    ok = await client.get(f"{API}/clinic/schedule/day?date={day}", headers=headers)
    assert ok.status_code == 200, ok.text

    await _set_feature(admin_client, "schedule.view_all", False)
    blocked = await client.get(f"{API}/clinic/schedule/day?date={day}", headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"] == "trial_restricted"
    await _set_feature(admin_client, "schedule.view_all", True)


async def test_me_permissions_filtered_on_trial(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """/auth/me на триале не показывает выключенные функции."""
    _, headers = await _provision_clinic(admin_client, client, "Триал Me")
    await _set_feature(admin_client, "price.edit", False)

    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert "price.edit" not in me.json()["permissions"]
    assert "price.view" in me.json()["permissions"]  # не выключали
    await _set_feature(admin_client, "price.edit", True)


async def test_paid_clinic_not_restricted(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Клиника вне триала (active) не подчиняется списку триал-функций."""
    clinic_id, headers = await _provision_clinic(admin_client, client, "Платная")
    await _set_status(_resources, clinic_id, SubscriptionStatus.ACTIVE)
    await _set_feature(admin_client, "patients.view", False)

    resp = await client.get(f"{API}/clinic/patients", headers=headers)
    assert resp.status_code == 200, resp.text  # не на триале — ограничения нет
    await _set_feature(admin_client, "patients.view", True)


# ── 2. Админ-настройка ──────────────────────────────────────────────────────


async def test_admin_lists_trial_features(admin_client: AsyncClient) -> None:
    """Свод триал-функций: весь каталог прав с флагом inTrial."""
    resp = await admin_client.get(f"{API}/admin/trial-features")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) > 10
    keys = {i["key"] for i in items}
    assert "schedule.view_all" in keys
    assert all("inTrial" in i for i in items)


async def test_admin_update_unknown_permission_is_422(admin_client: AsyncClient) -> None:
    """Неизвестный ключ права → 422, ничего не меняется."""
    resp = await admin_client.patch(
        f"{API}/admin/trial-features",
        json={"items": [{"key": "nope.bad", "inTrial": False}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "permission_not_found"


async def test_trial_features_admin_only(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Сотрудник клиники не имеет доступа к настройке триал-функций (нужен Skrezo)."""
    _, headers = await _provision_clinic(admin_client, client, "Триал Доступ")
    get_resp = await client.get(f"{API}/admin/trial-features", headers=headers)
    assert get_resp.status_code == 403, get_resp.text
    patch_resp = await client.patch(
        f"{API}/admin/trial-features",
        headers=headers,
        json={"items": [{"key": "patients.view", "inTrial": False}]},
    )
    assert patch_resp.status_code == 403, patch_resp.text


@pytest.mark.parametrize("method", ["get", "patch"])
async def test_trial_features_without_token_401(client: AsyncClient, method: str) -> None:
    """Ручки триал-функций закрыты авторизацией."""
    resp = await client.request(method, f"{API}/admin/trial-features", json={"items": []})
    assert resp.status_code == 401, resp.text
