from hmac import compare_digest
from os import environ
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from schemas.data_import import ImportRequest
from services.data_import import duplicate_tables

router = APIRouter(prefix="/api", tags=["Data import"])


@router.post("/dublicate")
async def duplicate(
    table: Annotated[str, Query(description="Таблицы Oracle через запятую")],
    filter: str | None = None,
    values: str | None = None,
    version: str | None = None,
    mode: Literal["replace", "append"] = "replace",
    x_import_token: Annotated[str | None, Header()] = None,
):
    expected = environ.get("DATA_IMPORT_TOKEN")
    if not expected:
        raise HTTPException(503, "Импорт не настроен: задайте DATA_IMPORT_TOKEN")
    if not x_import_token or not compare_digest(x_import_token.encode(), expected.encode()):
        raise HTTPException(403, "Неверный токен импорта")
    if version is not None and (filter is not None or values is not None):
        raise HTTPException(422, "version нельзя сочетать с filter/values")
    if (filter is None) != (values is None):
        raise HTTPException(422, "filter и values указываются вместе")
    keys = [key.strip() for key in filter.split(",")] if filter is not None else []
    items = values.split(",") if values is not None else []
    if len(keys) != len(items) or len({key.lower() for key in keys}) != len(keys):
        raise HTTPException(422, "Число фильтров и значений должно совпадать; фильтры не повторяются")
    try:
        request = ImportRequest(tables=[name.strip() for name in table.split(",")],
                                filters={"version": version} if version is not None else dict(zip(keys, items)), mode=mode)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    return await duplicate_tables(request)
