"""Тесты окончания триала: режим чтения и джоба перевода статуса.

Главный риск — деньги: раньше статус подписки не менял никто, и клиника после
30 дней работала бесплатно вечно. Отсюда две отдельные проверки:

* **доступ закрывается по дате**, а не по факту прогона джобы — иначе упавший
  воркер раздаёт бесплатный доступ. Поэтому режим чтения проверяется на
  подписке со статусом `trial`, но истёкшей датой;
* **джоба переводит статус** — чтобы админка и отчёты не показывали такого
  клиента активным триальщиком. Она идемпотентна: второй прогон никого не берёт.

Режим чтения не выключает клинику: карточки пациентов, расписание и отчёты
читаются, а `billing.*` доступен всегда — им клиент и выходит из блокировки.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.modules.access.application.services.subscription_policy import CLINIC_STATE_CACHE
from app.modules.subscriptions.application.use_cases.expire_trials import ExpireTrialsUseCase
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent
from tests.conftest import API, Resources, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070089{next(_phone_seq):04d}"


@pytest.fixture
async def clinic(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    """Клиника на триале: id, токен владельца, пациент и услуга."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Триал",
            "adminPhone": phone,
            "name": "Клиника Триал",
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
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Триалов"},
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


async def _end_trial(res: Resources, clinic_id: str) -> None:
    """Сдвинуть конец триала в прошлое, не трогая статус (как в жизни до джобы).

    Состояние подписки кешируется на 30 секунд, поэтому ключ сбрасывается явно —
    в проде то же самое делает TTL. Это и есть цена кеша: после конца триала
    ещё до полуминуты проходят записи.
    """
    async with res.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(clinic_id))
            .values(trial_ends_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    await res.fake.delete(CLINIC_STATE_CACHE.format(clinic_id))


def _book_body(clinic: dict[str, str]) -> dict[str, object]:
    return {
        "patientId": clinic["patient"],
        "doctorMembershipId": clinic["doctor"],
        "scheduledAt": working_slot(),
        "items": [{"serviceId": clinic["service"], "teeth": [46]}],
    }


# ── 1. Доступ закрывается по дате, без участия джобы ────────────────────────


async def test_expired_trial_blocks_writes_but_keeps_reads(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """31-й день: записать нельзя, но данные видны — клинику не выключают.

    Статус в БД всё ещё `trial` (джоба не прогонялась) — доступ считается по
    дате, иначе упавший воркер означал бы бесплатный доступ.
    """
    headers = {"Authorization": clinic["auth"]}
    before = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert before.status_code == 201, before.text

    await _end_trial(_resources, clinic["id"])

    blocked = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"] == "subscription_inactive"

    # Чтение живо: списки, расписание, отчёты.
    assert (await client.get(f"{API}/clinic/appointments", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/clinic/patients", headers=headers)).status_code == 200
    assert (
        await client.get(f"{API}/clinic/schedule/day?date=2026-07-20", headers=headers)
    ).status_code == 200
    assert (await client.get(f"{API}/clinic/reports/summary", headers=headers)).status_code == 200


async def test_cash_writes_are_blocked_too(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Касса тоже закрыта на запись: деньги мимо подписки не проводят."""
    headers = {"Authorization": clinic["auth"]}
    appointment = await client.post(
        f"{API}/clinic/appointments", headers=headers, json=_book_body(clinic)
    )
    appointment_id = appointment.json()["id"]

    await _end_trial(_resources, clinic["id"])

    payment = await client.post(
        f"{API}/clinic/cash/payments",
        headers=headers,
        json={"appointmentId": appointment_id, "amountTiyin": 1000, "method": "cash"},
    )
    assert payment.status_code == 403, payment.text
    assert payment.json()["error"] == "subscription_inactive"
    # Журнал кассы читается.
    assert (await client.get(f"{API}/clinic/cash/payments", headers=headers)).status_code == 200


async def test_billing_stays_available(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """`billing.*` не блокируется — иначе клиент заперт без способа продлить."""
    headers = {"Authorization": clinic["auth"]}
    await _end_trial(_resources, clinic["id"])

    resp = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["expired"] is True
    assert resp.json()["writesBlocked"] is True


async def test_me_hides_write_permissions(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Фронт рисует кнопки по /auth/me — мутирующих прав там больше нет."""
    headers = {"Authorization": clinic["auth"]}
    await _end_trial(_resources, clinic["id"])

    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    permissions = set(me.json()["permissions"])
    assert "appointments.create" not in permissions
    assert "cash.operate" not in permissions
    assert "patients.view" in permissions
    assert "billing.view" in permissions


# ── 2. Джоба переводит статус ───────────────────────────────────────────────


async def _run_job(res: Resources) -> int:
    async with res.factory() as session:
        return await ExpireTrialsUseCase(session, SubscriptionRepository(session)).execute()


async def test_job_moves_expired_trial_and_logs_event(
    _resources: Resources, client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Статус перестаёт врать: в админке клиника больше не «активный триальщик»."""
    await _end_trial(_resources, clinic["id"])
    assert await _run_job(_resources) == 1

    async with _resources.factory() as session:
        status = (
            await session.execute(
                select(Subscription.status).where(
                    Subscription.clinic_id == uuid.UUID(clinic["id"])
                )
            )
        ).scalar_one()
        assert status.value == "trial_expired"

        events = (
            (
                await session.execute(
                    select(SubscriptionEvent.to_status, SubscriptionEvent.reason)
                )
            )
            .all()
        )
    assert ("trial_expired", "trial_ended") in [(e[0].value, e[1]) for e in events]

    resp = await client.get(
        f"{API}/clinic/subscription", headers={"Authorization": clinic["auth"]}
    )
    assert resp.json()["status"] == "trial_expired"
    assert resp.json()["isTrial"] is False


async def test_job_is_idempotent_and_spares_live_trials(
    _resources: Resources, clinic: dict[str, str]
) -> None:
    """Живой триал не трогаем, а повторный прогон никого не берёт второй раз."""
    assert await _run_job(_resources) == 0  # триал ещё идёт

    await _end_trial(_resources, clinic["id"])
    assert await _run_job(_resources) == 1
    assert await _run_job(_resources) == 0
