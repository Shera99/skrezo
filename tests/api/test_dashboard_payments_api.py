"""API-тесты дашборда (реальные метрики) и платежей (заглушки формы)."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import update

from app.modules.clinics.application.services.provisioning_service import (
    ProvisionCommand,
    build_provisioning_service,
)
from app.shared.db.enums import ClinicType, SubscriptionStatus
from app.shared.db.models.subscription import Subscription
from tests.conftest import API, Resources


async def test_dashboard_summary_shape_and_counts(admin_client: AsyncClient) -> None:
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Метрика", "adminPhone": "0700700001"},
    )
    resp = await admin_client.get(f"{API}/admin/dashboard/summary?from=2026-07-01&to=2026-07-11")
    assert resp.status_code == 200
    body = resp.json()
    # Реальные метрики: один триал-соло → active/trial посчитаны.
    assert body["active"]["total"] == 1
    assert body["active"]["solo"] == 1
    assert body["trial"]["total"] == 1
    assert body["trial"]["noCard"] == 1
    # Биллинг-зависимые блоки — нули.
    assert body["mrr"]["value"] == 0
    assert body["risks"]["pastDue"] == 0
    assert "июля" in body["dateLabel"]


async def test_payments_journal_is_empty_for_a_quiet_period(admin_client: AsyncClient) -> None:
    """Период без платежей: пустой журнал и нулевая сводка; возврат неизвестного — 404."""
    resp = await admin_client.get(f"{API}/admin/payments?from=2026-07-01&to=2026-07-31")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["totalCount"] == 0
    assert body["summary"]["netTiyin"] == 0

    refund = await admin_client.post(
        f"{API}/admin/payments/{uuid.uuid4()}/refund", json={"reason": "тест"}
    )
    assert refund.status_code == 404
    assert refund.json()["error"] == "payment_not_found"


async def test_dashboard_counts_expired_trials_as_risk(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Клиника с истёкшим триалом видна в рисках, а не пропадает с экрана.

    До этого она не попадала ни в активные, ни в риски, ни в отменённые — то
    есть отток был не виден вовсе.
    """
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Истёкший", "adminPhone": "0700700002"},
    )
    assert created.status_code == 201, created.text
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(created.json()["id"]))
            .values(status=SubscriptionStatus.TRIAL_EXPIRED)
        )
        await session.commit()

    body = (
        await admin_client.get(f"{API}/admin/dashboard/summary?from=2026-07-01&to=2026-07-11")
    ).json()

    assert body["risks"]["trialExpired"] == 1
    assert body["active"]["total"] == 0  # и активной она больше не считается


async def test_dashboard_conversion_is_zero_for_an_empty_queue(admin_client: AsyncClient) -> None:
    """Заявок нет — конверсия 0 без деления на ноль."""
    body = (
        await admin_client.get(f"{API}/admin/dashboard/summary?from=2026-07-01&to=2026-07-11")
    ).json()

    assert body["conversion"] == {"pct": 0.0, "converted": 0, "leads": 0}


async def test_dashboard_conversion_counts_applications_of_the_single_queue(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Конверсия считается по очереди заявок: «заявок» — все строки, «сконвертировано» — с клиникой.

    Лиды с лендинга теперь живут в ``clinic_application``, поэтому и они — часть
    знаменателя; разобранные и отклонённые из него не выпадают. Клиника, заведённая
    по заявке (``ProvisionCommand.application_id``), попадает в числитель.
    """
    ids = []
    for i in range(3):
        resp = await public_client.post(
            f"{API}/leads", json={"fullName": f"Лид {i}", "phone": f"070082000{i}"}
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    await admin_client.patch(
        f"{API}/admin/clinic-applications/{ids[2]}/status", json={"status": "rejected"}
    )
    before = (
        await admin_client.get(f"{API}/admin/dashboard/summary?from=2026-07-01&to=2026-07-11")
    ).json()
    assert before["conversion"] == {"pct": 0.0, "converted": 0, "leads": 3}

    async with _resources.factory() as session:
        await build_provisioning_service(session).provision(
            ProvisionCommand(
                type=ClinicType.SOLO,
                admin_full_name="Лид 0",
                admin_phone="0700820000",
                application_id=uuid.UUID(ids[0]),
            )
        )

    after = (
        await admin_client.get(f"{API}/admin/dashboard/summary?from=2026-07-01&to=2026-07-11")
    ).json()
    assert after["conversion"] == {"pct": 33.3, "converted": 1, "leads": 3}
