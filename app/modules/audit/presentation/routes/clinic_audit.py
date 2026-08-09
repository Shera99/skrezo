"""Роут: GET /clinic/audit — журнал действий сотрудников клиники."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.use_cases.list_audit import ListAuditUseCase
from app.modules.audit.presentation.deps import get_list_audit_use_case
from app.modules.audit.presentation.schemas import AuditEntryOut, AuditListResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/audit",
    response_model=AuditListResponse,
    summary="Журнал действий клиники",
    response_description="Записи (свежие сверху) и курсор следующей страницы",
)
async def clinic_audit(
    actor_id: uuid.UUID | None = Query(default=None, alias="actor"),
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    context: ClientContext = Depends(require_permission("audit.view")),
    use_case: ListAuditUseCase = Depends(get_list_audit_use_case),
) -> AuditListResponse:
    """Кто из сотрудников что сделал: отмены, скидки, платежи, возвраты, правки.

    Отвечает на вопрос, на который клиника раньше ответить не могла. Скидка
    ограничена потолком роли, возврат — это деньги из кассы, и оба действия
    делает человек: без журнала спор «я такого не делал» упирался в ничью.

    Отбор идёт по клинике **из токена**, чужой журнал не запросить: параметра
    клиники здесь нет вовсе. Фильтры — по сотруднику (``actor``) и датам.
    Пишутся все мутации клиники: приёмы и их чек, касса, пациенты и
    одонтограмма, прайс, сотрудники, роли, расписание, настройки.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        actor_id=actor_id,
        clinic_scope=context.clinic_id,
        from_dt=from_dt,
        to_dt=to_dt,
        cursor=cursor,
        limit=limit,
    )
    return AuditListResponse(
        items=[AuditEntryOut.from_entry(e) for e in result.items],
        next_cursor=result.next_cursor,
    )
