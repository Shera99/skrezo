"""Роут: POST /payments/freedompay/result — серверный колбэк FreedomPay (публичный)."""

from __future__ import annotations

import secrets
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import settings
from app.modules.payments.application.use_cases.handle_freedompay_result import (
    RESULT_SCRIPT,
    HandleFreedomPayResultUseCase,
)
from app.modules.payments.presentation.deps import get_result_use_case
from app.shared.freedompay import sign

router = APIRouter()


def _xml_reply(status: str, description: str) -> str:
    """Ответ мерчанта по протоколу: статус, описание, соль и подпись (скрипт = result)."""
    fields = {"pg_status": status, "pg_description": description, "pg_salt": secrets.token_hex(8)}
    fields["pg_sig"] = sign(RESULT_SCRIPT, fields, settings.freedompay_secret_key)
    body = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in fields.items())
    return f'<?xml version="1.0" encoding="utf-8"?><response>{body}</response>'


@router.post(
    "/payments/freedompay/result",
    summary="Результат платежа (вызывает FreedomPay)",
    response_description="XML-ответ мерчанта: ok | rejected | error",
    response_class=Response,
)
async def freedompay_result(
    request: Request,
    use_case: HandleFreedomPayResultUseCase = Depends(get_result_use_case),
) -> Response:
    """Публичный `result_url` платёжной страницы. Подпись `pg_sig` сверяется
    первой; сумма — с нашей строкой платежа. `ok` — оплата учтена, период открыт
    (повтор колбэка тоже `ok`); `rejected` — сумма не совпала, провайдер отменит
    платёж; `error` — подпись или заказ не наши."""
    form = await request.form()
    params = {key: str(value) for key, value in form.items() if isinstance(value, str)}
    reply = await use_case.execute(params)
    return Response(
        content=_xml_reply(reply.status, reply.description), media_type="application/xml"
    )
