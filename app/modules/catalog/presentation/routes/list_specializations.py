"""Роут: GET /specializations — список специализаций (публичный)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.catalog.application.use_cases.list_specializations import (
    ListSpecializationsUseCase,
)
from app.modules.catalog.presentation.deps import get_list_specializations_use_case
from app.modules.catalog.presentation.schemas import CatalogItemOut
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()


@router.get(
    "/specializations",
    response_model=list[CatalogItemOut],
    summary="Список специализаций",
    response_description="Активные специализации на языке запроса",
)
async def list_specializations(
    lang: Lang = Depends(get_request_lang),
    use_case: ListSpecializationsUseCase = Depends(get_list_specializations_use_case),
) -> list[CatalogItemOut]:
    return await use_case.execute(lang)  # type: ignore[return-value]
