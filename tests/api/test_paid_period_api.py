"""Тесты оплаченного периода: отметка оплаты, истечение и блокировка.

Дыра, которую это закрывает, — денежная и зеркальная триалу. Триал закрывался по
дате, а оплаченный период — по статусу, который никто не выставлял: клиника с
кончившимся периодом сохраняла полный доступ, пока кто-нибудь не полез в БД.

Отсюда проверки:

* **доступ закрывается по дате `currentPeriodEnd`**, а не по прогону джобы —
  статус в БД всё ещё `active`, а записать уже нельзя;
* **отметка оплаты возвращает доступ сразу** — кеш состояния сбрасывается, иначе
  оплативший клиент до полуминуты сидит в режиме чтения;
* **джоба переводит в `past_due`** для честных данных и делает это однократно;
* **блокировка** закрывает запись решением Skrezo и снимается активацией.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.jobs.tasks import subscriptions as subscription_tasks
from app.modules.access.application.services.subscription_policy import CLINIC_STATE_CACHE
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent
from tests.conftest import API, Resources, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070096{next(_phone_seq):04d}"


@pytest.fixture
async def clinic(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    """Клиника с владельцем, пациентом и услугой — чтобы было чем записывать."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Оплата",
            "adminPhone": phone,
            "name": "Клиника Оплаченная",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Оплатов"},
    )
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 3000}
    )
    return {
        "id": str(created.json()["id"]),
        "auth": headers["Authorization"],
        "doctor": await own_membership_id(client, headers),
        "patient": str(patient.json()["id"]),
        "service": str(service.json()["id"]),
    }


def _book_body(clinic: dict[str, str]) -> dict[str, object]:
    return {
        "patientId": clinic["patient"],
        "doctorMembershipId": clinic["doctor"],
        "scheduledAt": working_slot(),
        "items": [{"serviceId": clinic["service"], "teeth": [46]}],
    }


async def _set_period_end(res: Resources, clinic_id: str, ends_at: datetime) -> None:
    """Сделать клинику оплаченной с заданным концом периода (мимо ручки)."""
    async with res.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(clinic_id))
            .values(
                status=SubscriptionStatus.ACTIVE,
                current_period_start=datetime.now(UTC) - timedelta(days=30),
                current_period_end=ends_at,
            )
        )
        await session.commit()
    await res.fake.delete(CLINIC_STATE_CACHE.format(clinic_id))


async def _status(res: Resources, clinic_id: str) -> SubscriptionStatus:
    async with res.factory() as session:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == uuid.UUID(clinic_id))
            )
        ).scalar_one()
        return subscription.status


# ── 1. Доступ закрывается датой, без участия джобы ──────────────────────────


async def test_expired_paid_period_blocks_writes_while_status_is_active(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Период кончился — записать нельзя, хотя в БД всё ещё `active`."""
    headers = {"Authorization": clinic["auth"]}
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) + timedelta(days=10))

    booked = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert booked.status_code == 201, booked.text

    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) - timedelta(minutes=1))

    blocked = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"] == "subscription_inactive"
    # Джоба не прогонялась: доступ закрыла дата, а не статус.
    assert await _status(_resources, clinic["id"]) is SubscriptionStatus.ACTIVE


async def test_expired_paid_period_keeps_reads(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Режим чтения: карточки и списки живы, клинику не выключают."""
    headers = {"Authorization": clinic["auth"]}
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) - timedelta(minutes=1))

    patients = await client.get(f"{API}/clinic/patients", headers=headers)
    assert patients.status_code == 200, patients.text

    subscription = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert subscription.status_code == 200, subscription.text
    assert subscription.json()["writesBlocked"] is True
    assert subscription.json()["expired"] is True


# ── 2. Отметка оплаты ───────────────────────────────────────────────────────


async def test_activation_restores_access_immediately(
    admin_client: AsyncClient,
    client: AsyncClient,
    _resources: Resources,
    clinic: dict[str, str],
) -> None:
    """Оплату отметили — записывать можно сразу, без ожидания истечения кеша."""
    headers = {"Authorization": clinic["auth"]}
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) - timedelta(minutes=1))
    blocked = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert blocked.status_code == 403, blocked.text

    paid_until = datetime.now(UTC) + timedelta(days=30)
    activated = await admin_client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/activate",
        json={"paidUntil": paid_until.isoformat()},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    assert activated.json()["writesBlocked"] is False

    allowed = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert allowed.status_code == 201, allowed.text


async def test_activation_from_expired_trial_works(
    admin_client: AsyncClient, _resources: Resources, clinic: dict[str, str]
) -> None:
    """Оплатить можно и из истёкшего триала — это основной путь клиента."""
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(clinic["id"]))
            .values(
                status=SubscriptionStatus.TRIAL_EXPIRED,
                trial_ends_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await session.commit()

    activated = await admin_client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/activate",
        json={"paidUntil": (datetime.now(UTC) + timedelta(days=30)).isoformat()},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    async with _resources.factory() as session:
        events = (
            await session.execute(
                select(SubscriptionEvent).order_by(SubscriptionEvent.created_at)
            )
        ).scalars()
        last = list(events)[-1]
        assert last.to_status is SubscriptionStatus.ACTIVE
        assert last.from_status is SubscriptionStatus.TRIAL_EXPIRED
        assert last.reason == "manual_payment"


async def test_activation_with_past_date_is_422(
    admin_client: AsyncClient, clinic: dict[str, str]
) -> None:
    """«Оплачено до вчера» — почти всегда опечатка, а не намерение."""
    resp = await admin_client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/activate",
        json={"paidUntil": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "paid_until_in_past"


async def test_activation_of_unknown_clinic_is_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        f"{API}/admin/clients/00000000-0000-4000-8000-000000000000/subscription/activate",
        json={"paidUntil": (datetime.now(UTC) + timedelta(days=30)).isoformat()},
    )
    assert resp.status_code == 404, resp.text


# ── 3. Джоба перевода статуса ───────────────────────────────────────────────


async def test_job_moves_expired_period_to_past_due(
    _resources: Resources, clinic: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Джоба приводит статус к реальности и делает это однократно."""
    monkeypatch.setattr(subscription_tasks, "session_factory", _resources.factory)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory",
        _resources.factory,
    )
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) - timedelta(hours=1))

    assert await subscription_tasks.expire_paid_periods({}) == 1
    assert await _status(_resources, clinic["id"]) is SubscriptionStatus.PAST_DUE
    assert await subscription_tasks.expire_paid_periods({}) == 0


async def test_job_ignores_active_period(
    _resources: Resources, clinic: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Действующий период джоба не трогает."""
    monkeypatch.setattr(subscription_tasks, "session_factory", _resources.factory)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory",
        _resources.factory,
    )
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) + timedelta(days=5))

    assert await subscription_tasks.expire_paid_periods({}) == 0
    assert await _status(_resources, clinic["id"]) is SubscriptionStatus.ACTIVE


# ── 4. Блокировка ───────────────────────────────────────────────────────────


async def test_block_closes_writes_and_activation_reopens(
    admin_client: AsyncClient,
    client: AsyncClient,
    _resources: Resources,
    clinic: dict[str, str],
) -> None:
    """Блок Skrezo закрывает запись; повторная активация её возвращает."""
    headers = {"Authorization": clinic["auth"]}
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) + timedelta(days=30))

    blocked = await admin_client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/block",
        json={"reason": "нарушение условий"},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["writesBlocked"] is True

    denied = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert denied.status_code == 403, denied.text

    reopened = await admin_client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/activate",
        json={"paidUntil": (datetime.now(UTC) + timedelta(days=30)).isoformat()},
    )
    assert reopened.status_code == 200, reopened.text

    allowed = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert allowed.status_code == 201, allowed.text


@pytest.mark.parametrize("action", ["activate", "block"])
async def test_subscription_management_needs_platform_admin(
    client: AsyncClient, clinic: dict[str, str], action: str
) -> None:
    """Клиника не управляет своей подпиской — это контур Skrezo."""
    resp = await client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/{action}",
        headers={"Authorization": clinic["auth"]},
        json={"paidUntil": (datetime.now(UTC) + timedelta(days=30)).isoformat()},
    )
    assert resp.status_code in (401, 403), resp.text


# ── 5. Предупреждение о конце оплаченного периода ───────────────────────────


async def test_period_warning_is_sent_once_and_resets_on_renewal(
    admin_client: AsyncClient,
    _resources: Resources,
    clinic: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Предупреждение — один раз за период, но НЕ один раз за жизнь клиента.

    Отметка сбрасывается при активации: без этого клиент получил бы
    предупреждение только про самый первый оплаченный период, а дальше — тишина
    до момента, когда касса встала.
    """
    monkeypatch.setattr(subscription_tasks, "session_factory", _resources.factory)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory",
        _resources.factory,
    )
    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) + timedelta(days=2))

    assert await subscription_tasks.warn_period_ending({}) == 1
    assert await subscription_tasks.warn_period_ending({}) == 0

    # Продлили — и следующий период снова получит предупреждение.
    renewed = await admin_client.post(
        f"{API}/admin/clients/{clinic['id']}/subscription/activate",
        json={"paidUntil": (datetime.now(UTC) + timedelta(days=2)).isoformat()},
    )
    assert renewed.status_code == 200, renewed.text
    assert await subscription_tasks.warn_period_ending({}) == 1


async def test_period_warning_ignores_trial_and_distant_periods(
    _resources: Resources, clinic: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Клинику на триале и далёкий период эта задача не трогает."""
    monkeypatch.setattr(subscription_tasks, "session_factory", _resources.factory)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory",
        _resources.factory,
    )
    # Клиника из фикстуры — на триале.
    assert await subscription_tasks.warn_period_ending({}) == 0

    await _set_period_end(_resources, clinic["id"], datetime.now(UTC) + timedelta(days=40))
    assert await subscription_tasks.warn_period_ending({}) == 0
