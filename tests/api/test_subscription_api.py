"""API-тесты подписки клиники (``GET /clinic/subscription``).

Ручка существует ради одного сценария: соло-врачу обещаны 30 дней триала, и на
31-й день он должен увидеть, что срок вышел. Отсюда и риски:

* подписка отдаётся своей клиники — из токена, а не из параметра;
* истёкший триал виден как истёкший (``expired``, ``daysLeft: 0``), иначе ручка
  бесполезна именно в тот день, ради которого сделана;
* право ``billing.view`` обязательно: врач кассу и подписку клиники не смотрит.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.shared.db.models.subscription import Subscription
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070096{next(_phone_seq):04d}"


@pytest.fixture
async def owner(admin_client: AsyncClient, client: AsyncClient) -> tuple[str, dict[str, str]]:
    """Клиника с триальной подпиской; вернуть её id и токен владельца."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Подписка",
            "adminPhone": phone,
            "name": "Клиника Подписка",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)
    return str(created.json()["id"]), headers


async def _shift_trial_end(res: Resources, clinic_id: str, ends_at: datetime) -> None:
    """Подвинуть конец триала — иначе истёкший триал в тесте не воспроизвести."""
    async with res.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(clinic_id))
            .values(trial_ends_at=ends_at)
        )
        await session.commit()


async def test_owner_sees_trial_with_days_left(
    client: AsyncClient, owner: tuple[str, dict[str, str]]
) -> None:
    """Свежая клиника на триале: срок = конец триала, дни остались, не истёк."""
    _, headers = owner
    resp = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "trial"
    assert body["isTrial"] is True
    assert body["expired"] is False
    assert body["daysLeft"] > 0
    assert body["expiresAt"] == body["trialEndsAt"]


async def test_expired_trial_is_visible(
    _resources: Resources, client: AsyncClient, owner: tuple[str, dict[str, str]]
) -> None:
    """31-й день: триал кончился — и клиника об этом узнаёт."""
    clinic_id, headers = owner
    await _shift_trial_end(_resources, clinic_id, datetime.now(UTC) - timedelta(days=1))

    resp = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["expired"] is True
    assert resp.json()["daysLeft"] == 0


async def test_worker_without_billing_view_is_403(
    client: AsyncClient, owner: tuple[str, dict[str, str]]
) -> None:
    """Врач подписку клиники не смотрит — у роли «Работник» нет billing.view."""
    _, headers = owner
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    worker = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач", "phone": phone, "specializationId": spec},
    )
    assert worker.status_code == 201, worker.text
    worker_headers = await otp_login(client, phone)

    resp = await client.get(f"{API}/clinic/subscription", headers=worker_headers)
    assert resp.status_code == 403, resp.text


async def test_without_token_is_401(client: AsyncClient) -> None:
    """Ручка закрыта авторизацией."""
    assert (await client.get(f"{API}/clinic/subscription")).status_code == 401
