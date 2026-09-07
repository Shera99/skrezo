"""Регрессия клиники (Ф6): кейсы из выученных ответов и прогоны."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.errors import ForbiddenError, NotFoundError
from app.core.jobs import RUN_AI_REGRESSION, JobQueue, get_job_queue
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.regression_cases import RegressionCaseService
from app.modules.ai.application.regression_runner import RegressionRunner
from app.modules.ai.infrastructure.repositories.regression_repository import RegressionRepository
from app.modules.ai.presentation.deps import get_regression_case_service, get_regression_runner
from app.modules.ai.presentation.schemas import (
    CreateRegressionCaseRequest,
    RegressionCaseCounts,
    RegressionCaseOut,
    RegressionCasesResponse,
    RegressionResultOut,
    RegressionRunDetail,
    RegressionRunOut,
    RegressionRunsResponse,
    StartRegressionRunRequest,
    UpdateRegressionCaseRequest,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.shared.auth import ClientContext

router = APIRouter()

_CASE_404: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Кейс не найден или принадлежит другой клинике",
        "content": {
            "application/json": {
                "example": {"error": "case_not_found", "message": "Кейс не найден"}
            }
        },
    },
}
_RUN_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Прогон уже идёт",
        "content": {
            "application/json": {
                "example": {
                    "error": "regression_run_active",
                    "message": "Прогон уже идёт — дождитесь его завершения",
                }
            }
        },
    },
}


def _clinic(context: ClientContext) -> uuid.UUID:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return context.clinic_id


# ── Кейсы ───────────────────────────────────────────────────────────────────


@router.get(
    "/clinic/regression/cases",
    response_model=RegressionCasesResponse,
    summary="Кейсы регрессии клиники",
    response_description="Страница кейсов (новые сверху) и счётчики",
)
async def list_regression_cases(
    kind: str | None = Query(default=None, pattern="^(knowledge|reply|refusal|medical)$"),
    active: bool | None = Query(default=None),
    result: str | None = Query(
        default=None, pattern="^(passed|failed|skipped)$", description="По итогу последнего прогона"
    ),
    q: str | None = Query(default=None, max_length=100, description="Поиск по вопросу и заметке"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: RegressionCaseService = Depends(get_regression_case_service),
) -> RegressionCasesResponse:
    """Каждый одобренный ответ, выученный вопрос и инцидент — кейс. Кейс уникален по
    виду и вопросу: повтор обновляет ожидание, а не плодит дубли."""
    clinic_id = _clinic(context)
    repository = RegressionRepository(service._session)
    rows, total = await repository.list_cases(
        clinic_id,
        kind=kind,
        active=active,
        result=result,
        query=(q or "").strip() or None,
        page=page,
        page_size=page_size,
    )
    counts = await repository.case_counts(clinic_id)
    return RegressionCasesResponse(
        list=[RegressionCaseOut.from_orm_case(c) for c in rows],
        counts=RegressionCaseCounts(**counts),
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.post(
    "/clinic/regression/cases",
    status_code=status.HTTP_201_CREATED,
    response_model=RegressionCaseOut,
    summary="Ручной кейс",
    responses={
        422: {
            "description": "Вид или ожидание вне словаря, нет эталона",
            "content": {
                "application/json": {
                    "example": {
                        "error": "case_expect_invalid",
                        "message": "Нужен эталонный ответ или ключевые слова",
                    }
                }
            },
        }
    },
)
async def create_regression_case(
    body: CreateRegressionCaseRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: RegressionCaseService = Depends(get_regression_case_service),
    audit: AuditService = Depends(get_audit_service),
) -> RegressionCaseOut:
    """Когда эталон известен без диалога: knowledge/reply — эталонный ответ или
    ключевые слова; refusal — выдуманный ответ, который валидатор обязан снять;
    medical — ожидание фильтра."""
    clinic_id = _clinic(context)
    case = await service.create_manual(
        clinic_id,
        actor_id=context.account_id,
        kind=body.kind,
        lang=body.lang,
        question=body.question,
        expected_reply=body.expected_reply,
        keywords=body.keywords,
        medical=body.medical,
        proposed=body.proposed,
        note=body.note,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="regression_case.create",
        entity_type="regression_case",
        entity_id=case.id,
        payload={"kind": case.kind},
    )
    return RegressionCaseOut.from_orm_case(case)


@router.patch(
    "/clinic/regression/cases/{case_id}",
    response_model=RegressionCaseOut,
    summary="Выключить/включить кейс, заметка",
    responses=_CASE_404,
)
async def update_regression_case(
    case_id: uuid.UUID,
    body: UpdateRegressionCaseRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: RegressionCaseService = Depends(get_regression_case_service),
) -> RegressionCaseOut:
    """Выключенный кейс в прогон не попадает и от новых одобрений не воскресает."""
    case = await service.update(
        _clinic(context), case_id, fields=body.model_dump(exclude_unset=True)
    )
    return RegressionCaseOut.from_orm_case(case)


@router.delete(
    "/clinic/regression/cases/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить кейс",
    responses=_CASE_404,
)
async def delete_regression_case(
    case_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: RegressionCaseService = Depends(get_regression_case_service),
) -> Response:
    """Вместе с итогами прошлых прогонов по нему."""
    await service.delete(_clinic(context), case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Прогоны ─────────────────────────────────────────────────────────────────


@router.post(
    "/clinic/regression/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RegressionRunOut,
    summary="Запустить прогон",
    response_description="Прогон поставлен (queued) или выполнен на месте",
    responses=_RUN_ERRORS,
)
async def start_regression_run(
    body: StartRegressionRunRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    runner: RegressionRunner = Depends(get_regression_runner),
    queue: JobQueue = Depends(get_job_queue),
    audit: AuditService = Depends(get_audit_service),
) -> RegressionRunOut:
    """Перед сменой промпта, базы знаний, глоссария или модели. `offline` — без модели
    и бесплатно (медфильтр, валидатор на снятой выдумке, наличие знания в промпте);
    `model` — сухой прогон модели по каждому кейсу: ничего не уходит пациентам,
    токены учитываются клинике. Выполняется в воркере — опрашивайте прогон."""
    clinic_id = _clinic(context)
    run = await runner.create_run(clinic_id, mode=body.mode, actor_id=context.account_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="regression_run.start",
        entity_type="regression_run",
        entity_id=run.id,
        payload={"mode": run.mode},
    )
    if not await queue.enqueue(RUN_AI_REGRESSION, str(run.id)):
        # Очереди нет (локальный стенд, тесты) — прогон на месте.
        await runner.run(run.id)
    return RegressionRunOut.from_orm_run(run)


@router.get(
    "/clinic/regression/runs",
    response_model=RegressionRunsResponse,
    summary="История прогонов",
)
async def list_regression_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: RegressionCaseService = Depends(get_regression_case_service),
) -> RegressionRunsResponse:
    """Новые сверху; у каждого — версия промпта и модель, на которых прогонялось."""
    rows, total = await RegressionRepository(service._session).list_runs(
        _clinic(context), page=page, page_size=page_size
    )
    return RegressionRunsResponse(
        list=[RegressionRunOut.from_orm_run(r) for r in rows],
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.get(
    "/clinic/regression/runs/{run_id}",
    response_model=RegressionRunDetail,
    summary="Прогон с итогами по кейсам",
    responses={
        404: {
            "description": "Прогон не найден",
            "content": {
                "application/json": {
                    "example": {"error": "regression_run_not_found", "message": "Прогон не найден"}
                }
            },
        }
    },
)
async def get_regression_run(
    run_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: RegressionCaseService = Depends(get_regression_case_service),
) -> RegressionRunDetail:
    """Проваленные кейсы первыми: что ожидали, что получили, заметка валидатора."""
    repository = RegressionRepository(service._session)
    run = await repository.get_run(_clinic(context), run_id)
    if run is None:
        raise NotFoundError("Прогон не найден", code="regression_run_not_found")
    results = await repository.results(run.id)
    return RegressionRunDetail(
        **RegressionRunOut.from_orm_run(run).model_dump(),
        results=[
            RegressionResultOut(
                case_id=case.id,
                kind=case.kind,
                question=case.question,
                status=result.status,
                expected=result.expected,
                actual=result.actual,
                note=result.note,
                latency_ms=result.latency_ms,
            )
            for result, case in results
        ],
    )
