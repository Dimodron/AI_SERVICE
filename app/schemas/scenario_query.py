from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "like", "is_null", "is_not_null"] = "eq"
    value: str | StrictInt | StrictFloat | StrictBool | None = None


class QueryAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: Literal["sum", "count", "avg", "min", "max"]
    column: str = Field(min_length=1, description="Имя колонки; * разрешено только для count.")


class ScenarioQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID
    columns: list[str] = Field(default_factory=list, max_length=30)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=20)
    aggregates: list[QueryAggregate] = Field(default_factory=list, max_length=10)
    group_by: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1, le=200, strict=True)
