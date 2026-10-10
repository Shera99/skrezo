"""API-тесты трендов дашборда (ряды по месяцам)."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient

from app.shared.db.models.clinic_application import ClinicApplication
from tests.conftest import API, Resources, otp_code


async def test_trends_shape_and_current_month(admin_client: AsyncClient) -> None:
    # Один соло-клиент и одна заявка — попадают в текущий (последний) месяц.
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Тренд", "adminPhone": "0700800001"},
    )
    lead = await admin_client.post(
        f"{API}/leads",
        json={"fullName": "Заявка", "phone": "0700800002", "clinicName": "Клиника"},
    )
    assert lead.status_code == 201, lead.text

    resp = await admin_client.get(f"{API}/admin/dashboard/trends?months=6")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["mrr"]) == 6
    assert len(body["activeClients"]) == 6
    assert len(body["leadsToClients"]) == 6
    assert all(p["value"] == 0 for p in body["mrr"])  # MRR — заглушка (биллинг)

    # Текущий месяц — последний бакет.
    assert body["leadsToClients"][-1]["clients"] == 1
    assert body["leadsToClients"][-1]["leads"] == 1
    assert body["activeClients"][-1] == {
        "month": body["activeClients"][-1]["month"],
        "solo": 1,
        "clinic": 0,
    }
    assert body["soloClinicRatio"] == {"solo": 1, "clinic": 0}


async def test_trends_months_param(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"{API}/admin/dashboard/trends?months=12")
    assert resp.status_code == 200
    assert len(resp.json()["mrr"]) == 12
    # Пустая база — везде нули и пустой срез.
    assert resp.json()["soloClinicRatio"] == {"solo": 0, "clinic": 0}
    assert all(p["clients"] == 0 for p in resp.json()["leadsToClients"])


async def _otp_application(client: AsyncClient, phone: str) -> None:
    """Заявка через OTP (дверь №1) — телефон подтверждён кодом."""
    assert (await client.post(f"{API}/auth/otp/request", json={"phone": phone})).status_code == 200
    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    city = (await client.get(f"{API}/cities")).json()[0]["id"]
    submitted = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": verified.json()["registrationToken"],
            "companyName": "Из приложения",
            "cityId": city,
            "doctorsCount": "2-5",
        },
    )
    assert submitted.status_code == 201, submitted.text


async def test_trends_leads_count_applications_of_both_doors(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """В «Заявках» тренда — и лиды с лендинга, и заявки через OTP (очередь одна).

    Разобранные (``reviewed``/``rejected``) тоже считаются: это «сколько заявок
    пришло», а не «сколько ждёт».
    """
    landing = await public_client.post(
        f"{API}/leads", json={"fullName": "Лид", "phone": "0700810001"}
    )
    assert landing.status_code == 201, landing.text
    await _otp_application(public_client, "0700810002")
    rejected = await public_client.post(
        f"{API}/leads", json={"fullName": "Отклонённый", "phone": "0700810003"}
    )
    await admin_client.patch(
        f"{API}/admin/clinic-applications/{rejected.json()['id']}/status",
        json={"status": "rejected"},
    )

    body = (await admin_client.get(f"{API}/admin/dashboard/trends?months=6")).json()

    assert body["leadsToClients"][-1]["leads"] == 3
    assert sum(p["leads"] for p in body["leadsToClients"][:-1]) == 0


async def test_trends_leads_are_bucketed_by_month_of_creation(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Заявка прошлых месяцев попадает в свой бакет, а не в текущий."""
    now = datetime.now(UTC)
    year, month = now.year, now.month - 2
    if month < 1:
        year, month = year - 1, month + 12
    two_months_ago = datetime(year, month, 15, 12, tzinfo=UTC)
    async with _resources.factory() as session:
        session.add_all(
            [
                ClinicApplication(
                    phone="+996700810010",
                    phone_verified=False,
                    full_name="Давний лид",
                    created_at=two_months_ago,
                ),
                ClinicApplication(
                    phone="+996700810011",
                    phone_verified=False,
                    full_name="Давний лид 2",
                    created_at=two_months_ago,
                ),
            ]
        )
        await session.commit()

    body = (await admin_client.get(f"{API}/admin/dashboard/trends?months=6")).json()

    by_month = {p["month"]: p["leads"] for p in body["leadsToClients"]}
    assert by_month[f"{two_months_ago:%Y-%m}"] == 2
    assert by_month[f"{now:%Y-%m}"] == 0
    assert sum(by_month.values()) == 2
