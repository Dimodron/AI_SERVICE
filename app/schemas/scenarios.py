from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    model_validator,
)

Jurpers = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1)
    description: str | None = None
    table_name: str | None = None
    columns_description: dict[str, JsonValue] = Field(default_factory=dict)
    scenario: str = Field(min_length=1)
    visible_jurpers: list[Jurpers] = Field(default_factory=list, max_length=1000, description="Юрлица, которым доступен сценарий. Пустой список — всем.")
    is_active: StrictBool = True
    create_user: UUID | None = Field(default=None, description="ID автора из таблицы users.")


class ScenarioUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    table_name: str | None = None
    columns_description: dict[str, JsonValue] | None = None
    scenario: str | None = Field(default=None, min_length=1)
    visible_jurpers: list[Jurpers] | None = Field(default=None, max_length=1000)
    is_active: StrictBool | None = None
    edit_user: UUID | None = Field(default=None, description="ID редактора из таблицы users.")

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("Укажите хотя бы одно поле для изменения")
        for field in ("title", "columns_description", "scenario", "is_active", "visible_jurpers"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} не может быть null")
        return self


class ScenarioResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    table_name: str | None
    columns_description: dict[str, JsonValue]
    scenario: str
    visible_jurpers: list[int]
    is_active: bool
    create_user: UUID | None
    edit_user: UUID | None
    create_time: datetime
    edit_time: datetime
