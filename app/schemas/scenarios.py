from datetime import datetime
from schemas.report_template import ReportTemplate, validate_template_sources
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

GroupName = Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]

Jurpers = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]


class ScenarioTable(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    table_name: str = Field(min_length=1, max_length=127, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}(\.[a-zA-Z_][a-zA-Z0-9_]{0,62})?$")
    description: str = Field(default="", max_length=10000)
    columns_description: dict[str, JsonValue] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_columns(self):
        import re
        if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", name) for name in self.columns_description):
            raise ValueError("columns_description: ожидаются имена колонок")
        return self


def validate_tables(tables):
    names = [item.table_name if "." in item.table_name else "public." + item.table_name for item in tables]
    if len(names) != len(set(names)):
        raise ValueError("Таблицы в сценарии не должны повторяться")


class ScenarioCreate(BaseModel):
    report_template: ReportTemplate | None = None
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1)
    description: str | None = None
    table_name: str | None = None
    columns_description: dict[str, JsonValue] = Field(default_factory=dict)
    tables: list[ScenarioTable] = Field(default_factory=list, max_length=30, description="Таблицы с описаниями. Непустой список используется вместо старых table_name и columns_description.")
    scenario: str = Field(min_length=1)
    visible_jurpers: list[Jurpers] = Field(default_factory=list, max_length=1000, description="Юрлица, которым доступен сценарий. Пустой список — всем.")
    groups: list[GroupName] = Field(default_factory=list, max_length=50, description="Категории сценария, например Администрирование и Аналитика.")
    is_admin: StrictBool = Field(default=False, description="Сценарий доступен для выполнения только администраторам.")
    is_active: StrictBool = True
    create_user: str | None = Field(default=None, min_length=1, max_length=200, description="Логин автора из таблицы users.", examples=["ZVEREV"])


    @model_validator(mode="after")
    def validate_table_list(self):
        validate_tables(self.tables)
        validate_template_sources(self.report_template, self.tables, self.table_name, self.columns_description)
        return self


class ScenarioUpdate(BaseModel):
    report_template: ReportTemplate | None = None
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    table_name: str | None = None
    columns_description: dict[str, JsonValue] | None = None
    tables: list[ScenarioTable] | None = Field(default=None, max_length=30)
    scenario: str | None = Field(default=None, min_length=1)
    visible_jurpers: list[Jurpers] | None = Field(default=None, max_length=1000)
    groups: list[GroupName] | None = Field(default=None, max_length=50)
    is_admin: StrictBool | None = None
    is_active: StrictBool | None = None
    edit_user: str | None = Field(default=None, min_length=1, max_length=200, description="Логин редактора из таблицы users.", examples=["ZVEREV"])

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("Укажите хотя бы одно поле для изменения")
        for field in ("title", "columns_description", "scenario", "is_active", "visible_jurpers", "groups", "is_admin", "tables"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} не может быть null")
        if self.tables is not None:
            validate_tables(self.tables)
        return self


class ScenarioResponse(BaseModel):
    report_template: ReportTemplate | None = None
    id: UUID
    title: str
    description: str | None
    table_name: str | None
    columns_description: dict[str, JsonValue]
    tables: list[ScenarioTable]
    scenario: str
    visible_jurpers: list[int]
    groups: list[str]
    is_admin: bool
    is_active: bool
    create_user: str | None
    edit_user: str | None
    create_time: datetime
    edit_time: datetime
