"""FreedomPay: подпись по документации и клиент платёжной страницы без сети."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from app.shared.freedompay import (
    FreedomPayError,
    FreedomPayHttpClient,
    FreedomPayRejectedError,
    InitPaymentRequest,
    sign,
    verify,
)


def test_signature_matches_documentation_example() -> None:
    """Строка из документации: init_payment.php;25;Order description;<merchant>;23;salt;<secret>."""
    params = {
        "pg_amount": "25",
        "pg_description": "Order description",
        "pg_merchant_id": "12345",
        "pg_order_id": "23",
        "pg_salt": "some_random_string",
    }
    expected = hashlib.md5(
        b"init_payment.php;25;Order description;12345;23;some_random_string;SECRET"
    ).hexdigest()
    assert sign("init_payment.php", params, "SECRET") == expected
    assert verify("init_payment.php", {**params, "pg_sig": expected}, "SECRET") is True
    assert verify("init_payment.php", {**params, "pg_sig": expected}, "OTHER") is False


def _request() -> InitPaymentRequest:
    return InitPaymentRequest(
        order_id="order-1",
        amount="3000.00",
        currency="KGS",
        description="Skrezo: подписка",
        result_url="https://api.test/api/v1/payments/freedompay/result",
        success_url="https://app.test/billing/success",
        failure_url=None,
        user_phone="996700000000",
        user_email=None,
        language="ru",
        testing_mode=True,
        lifetime_seconds=1800,
        extra={"pg_param1": "clinic"},
    )


def _xml(fields: dict[str, str]) -> str:
    return "<response>" + "".join(f"<{k}>{v}</{k}>" for k, v in fields.items()) + "</response>"


@pytest.mark.asyncio
async def test_init_payment_signs_form_and_verifies_signed_reply() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen.append(form)
        assert request.url.path == "/init_payment.php"
        assert verify("init_payment.php", form, "SECRET")
        reply = {
            "pg_status": "ok",
            "pg_payment_id": "987",
            "pg_redirect_url": "https://pay.test/page",
            "pg_salt": "reply-salt",
        }
        reply["pg_sig"] = sign("init_payment.php", reply, "SECRET")
        return httpx.Response(200, text=_xml(reply))

    client = FreedomPayHttpClient(
        "https://api.freedompay.kg",
        "12345",
        "SECRET",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await client.init_payment(_request())
    assert result.payment_id == "987" and result.redirect_url == "https://pay.test/page"
    sent = seen[0]
    assert sent["pg_merchant_id"] == "12345" and sent["pg_amount"] == "3000.00"
    assert sent["pg_testing_mode"] == "1" and sent["pg_request_method"] == "POST"
    assert sent["pg_result_url"].endswith("/payments/freedompay/result")
    assert (
        sent["pg_success_url"] == "https://app.test/billing/success"
        and "pg_failure_url" not in sent
    )
    assert sent["pg_user_phone"] == "996700000000" and sent["pg_param1"] == "clinic"


@pytest.mark.asyncio
async def test_init_payment_rejects_bad_reply_signature_and_provider_errors() -> None:
    def forged(request: httpx.Request) -> httpx.Response:
        reply = {
            "pg_status": "ok",
            "pg_payment_id": "1",
            "pg_redirect_url": "https://evil",
            "pg_salt": "s",
        }
        reply["pg_sig"] = sign("init_payment.php", reply, "WRONG")
        return httpx.Response(200, text=_xml(reply))

    def declined(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_xml(
                {
                    "pg_status": "error",
                    "pg_error_code": "101",
                    "pg_error_description": "Merchant not found",
                }
            ),
        )

    for handler, fragment in ((forged, "подпись"), (declined, "101")):
        client = FreedomPayHttpClient(
            "https://api.freedompay.kg",
            "12345",
            "SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(FreedomPayError) as exc:
            await client.init_payment(_request())
        assert fragment in str(exc.value)


@pytest.mark.asyncio
async def test_return_methods_are_sent_signed_and_rejection_has_its_own_class() -> None:
    """Методы возврата уходят в форму и в подпись; явный отказ — FreedomPayRejectedError."""
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen.append(form)
        assert verify("init_payment.php", form, "SECRET")  # подписаны и новые поля
        reply = {
            "pg_status": "error",
            "pg_error_code": "340",
            "pg_error_description": "Invalid pg_success_url_method",
        }
        return httpx.Response(200, text=_xml(reply))

    client = FreedomPayHttpClient(
        "https://api.freedompay.kg",
        "12345",
        "SECRET",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    request = replace(
        _request(),
        failure_url="https://office.skrezo.com/pay?payment=order-1",
        success_url_method="AUTOGET",
        failure_url_method="AUTOGET",
    )
    with pytest.raises(FreedomPayRejectedError) as exc:
        await client.init_payment(request)
    assert exc.value.code == "340" and "pg_success_url_method" in str(exc.value)
    assert seen[0]["pg_success_url_method"] == "AUTOGET"
    assert seen[0]["pg_failure_url_method"] == "AUTOGET"
    assert seen[0]["pg_failure_url"] == "https://office.skrezo.com/pay?payment=order-1"

    # Без метода поле не передаётся вовсе — провайдер решает сам.
    seen.clear()
    with pytest.raises(FreedomPayRejectedError):
        await client.init_payment(_request())
    assert "pg_success_url_method" not in seen[0] and "pg_failure_url_method" not in seen[0]


@pytest.mark.asyncio
async def test_only_explicit_error_status_counts_as_rejection() -> None:
    """Пустой статус (чужой XML) и pending — не отказ: такой запрос повторять нельзя."""
    for status in ("", "pending"):

        def reply(request: httpx.Request, status: str = status) -> httpx.Response:
            return httpx.Response(200, text=_xml({"pg_status": status}))

        client = FreedomPayHttpClient(
            "https://api.freedompay.kg",
            "12345",
            "SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        )
        with pytest.raises(FreedomPayError) as exc:
            await client.init_payment(_request())
        assert not isinstance(exc.value, FreedomPayRejectedError)
