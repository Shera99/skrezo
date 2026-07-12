"""Роут: GET /cities — список городов (публичный)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.catalog.application.use_cases.list_cities import ListCitiesUseCase
from app.modules.catalog.presentation.deps import get_list_cities_use_case
from app.modules.catalog.presentation.schemas import CatalogItemOut
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()


@router.get(
    "/cities",
    response_model=list[CatalogItemOut],
    summary="Список городов",
    response_description="Активные города на языке запроса",
)
async def list_cities(
    lang: Lang = Depends(get_request_lang),
    use_case: ListCitiesUseCase = Depends(get_list_cities_use_case),
) -> list[CatalogItemOut]:
    return await use_case.execute(lang)  # type: ignore[return-value]
