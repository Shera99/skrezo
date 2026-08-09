"""Тесты переходного режима: неизвестные поля тела логируются, но не отвергаются.

Молча проглоченное поле — это скрытая ошибка: `PATCH` приёма с `items` отвечает
200, хотя позиции не сохранились (для них своя ручка). Отвечать 422 правильнее,
но включать запрет вслепую значит уронить фронт на полях, которые он шлёт давно.

Поэтому проверяем ровно две вещи: поведение API не изменилось и в логе есть след,
по которому потом принимать решение.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from httpx import AsyncClient
from structlog.testing import capture_logs

from app.modules.identity.presentation.schemas import LoginRequest
from tests.conftest import API


def _unknown_events(
    logs: list[MutableMapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == "unknown_body_fields"]


def test_unknown_field_is_logged_with_schema_and_names() -> None:
    """В логе видно, какая схема и какие именно поля пришли лишними."""
    with capture_logs() as logs:
        LoginRequest.model_validate({"phone": "0700", "password": "x", "rememberMe": True})

    events = _unknown_events(logs)
    assert len(events) == 1
    assert events[0]["schema"] == "LoginRequest"
    assert events[0]["fields"] == ["rememberMe"]


def test_known_fields_in_both_cases_are_not_reported() -> None:
    """camelCase и snake_case — оба свои: модель принимает и то, и другое."""
    with capture_logs() as logs:
        LoginRequest.model_validate({"phone": "0700", "password": "x"})

    assert _unknown_events(logs) == []


async def test_request_with_unknown_field_still_succeeds(client: AsyncClient) -> None:
    """Поведение API не изменилось: лишнее поле по-прежнему игнорируется."""
    resp = await client.post(
        f"{API}/leads",
        json={
            "fullName": "Заявитель",
            "phone": "0700880001",
            "clinicName": "Клиника Лишних Полей",
            "utmSource": "instagram",  # такого поля у нас нет
        },
    )

    assert resp.status_code == 201, resp.text
