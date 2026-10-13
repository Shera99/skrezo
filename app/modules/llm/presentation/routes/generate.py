"""Роут: POST /clinic/llm/generate — обращение голосового помощника приложения к модели."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.modules.llm.application.use_cases.generate import GenerateUseCase
from app.modules.llm.presentation.deps import get_generate_use_case, require_llm_access
from app.modules.llm.presentation.schemas import GENERATE_ERRORS, GENERATE_REQUEST_BODY
from app.shared.auth import ClientContext

router = APIRouter()


@router.post(
    "/clinic/llm/generate",
    response_class=JSONResponse,
    summary="Обращение голосового помощника к модели",
    response_description="Ответ Gemini `generateContent` без изменений",
    responses=GENERATE_ERRORS,
    openapi_extra=GENERATE_REQUEST_BODY,
)
async def llm_generate(
    request: Request,
    context: ClientContext = Depends(require_llm_access),
    use_case: GenerateUseCase = Depends(get_generate_use_case),
) -> JSONResponse:
    """Прокси к Gemini для задач приложения: `voice_agent` и `voice_classify`.

    Тело собирает приложение — задача и поля Gemini (`contents`,
    `systemInstruction`, `tools`, `generationConfig`). Распознавание речи —
    только на устройстве: сюда приходит текст, вложения отклоняются. Сервер
    проверяет тело до обращения к Google, сам выбирает модель, ставит длину
    ответа и «размышления», считает лимиты и токены на клинику. Ответ — JSON
    Gemini как есть, без стриминга.

    **Цикл `voice_agent`.** У моделей Gemini 3 в части с `functionCall`
    приходит `thoughtSignature`: в следующем обращении ответ модели нужно
    положить в `contents` целиком, иначе Google отклонит запрос
    (`422 llm_request_rejected`).

    **Лимиты** — каждое обращение: 20 в минуту и 150 в час на сотрудника, 1500 в
    сутки на клинику (числа — в настройках сервера). **Таймаут** одного
    обращения: агент 4 с, классификатор 6 с; повторов нет. Доступ — любой
    сотрудник клиники; в режиме чтения подписки — `403 subscription_inactive`.
    Тексты запросов и ответов не логируются и не хранятся.
    """
    clinic_id = context.clinic_id
    assert clinic_id is not None  # get_clinic_context пускает только с выбранной клиникой
    data = await use_case.execute(
        account_id=context.account_id, clinic_id=clinic_id, raw_body=await request.body()
    )
    return JSONResponse(data)
