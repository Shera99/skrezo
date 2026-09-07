"""Ф8: обход номеров WhatsApp вынесен из 5-минутной проверки в часовую."""

from __future__ import annotations

from typing import Any

from app.modules.ai.application.health import AiHealthService
from app.shared.whatsapp_cloud import SentMessage
from tests.api.conftest import Ctx
from tests.conftest import Resources


class _CountingWa:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.checked: list[str] = []

    async def check_token(self, phone_number_id: str) -> bool:
        self.checked.append(phone_number_id)
        return self.alive

    async def send_text(self, *a: Any, **k: Any) -> SentMessage:
        return SentMessage(wa_message_id="x")

    async def send_template(self, *a: Any, **k: Any) -> SentMessage:
        return SentMessage(wa_message_id="x")

    async def send_confirm_button(self, *a: Any, **k: Any) -> SentMessage:
        return SentMessage(wa_message_id="x")

    async def fetch_media(self, media_id: str) -> Any:
        return None

    async def fetch_templates(self, waba_id: str) -> Any:
        return None


async def test_five_minute_check_skips_tokens_and_hourly_check_walks_bindings(
    ctx: Ctx, _resources: Resources
) -> None:
    wa = _CountingWa()
    async with _resources.factory() as session:
        report = await AiHealthService(session, None, wa).check(tokens=False)
        assert report.tokens_failed == [] and wa.checked == []

        failed = await AiHealthService(session, None, wa).check_tokens()
        assert failed == [] and wa.checked == [ctx.phone_number_id]

        dead = _CountingWa(alive=False)
        assert await AiHealthService(session, None, dead).check_tokens() == [ctx.phone_number_id]
