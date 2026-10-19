"""Юнит-тесты отказов доставки WhatsApp: код Meta → объяснение и постоянство."""

from __future__ import annotations

import httpx
import pytest

from app.modules.whatsapp.domain.delivery import describe_failure
from app.shared.whatsapp_cloud.client import WhatsAppSendError
from app.shared.whatsapp_cloud.graph_client import GraphWhatsAppClient


@pytest.mark.parametrize(
    ("status", "code", "permanent", "fragment"),
    [
        (400, 131030, True, "не в списке разрешённых получателей"),
        (400, 131026, True, "нет WhatsApp"),
        (400, 190, True, "переподключите WhatsApp"),
        (429, 130429, False, "лимит отправки Meta"),
        (400, 131056, False, "Meta просит подождать"),
        (500, None, False, "HTTP 500"),
        (None, None, False, "нет связи с WhatsApp"),
        (400, 999_999, True, "код 999999"),
    ],
)
def test_meta_error_is_explained(
    status: int | None, code: int | None, permanent: bool, fragment: str
) -> None:
    failure = describe_failure(status=status, code=code, detail="raw meta text")

    assert failure.text.startswith("WhatsApp не доставил сообщение: ")
    assert fragment in failure.text
    assert failure.permanent is permanent
    assert failure.code == code


async def test_graph_400_carries_meta_code_and_details() -> None:
    """Код и текст Meta доходят до кода приложения, а не теряются в «Graph API 400»."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "(#131030) Recipient phone number not in allowed list",
                    "type": "OAuthException",
                    "code": 131030,
                    "error_data": {
                        "messaging_product": "whatsapp",
                        "details": "Recipient phone number not in allowed list",
                    },
                }
            },
        )

    client = GraphWhatsAppClient(
        "https://graph.test/v21.0",
        "token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(WhatsAppSendError) as caught:
        await client.send_text("123", "996700000000", "Здравствуйте")

    assert caught.value.status == 400
    assert caught.value.code == 131030
    assert caught.value.detail == "Recipient phone number not in allowed list"
    assert "131030" in str(caught.value)
