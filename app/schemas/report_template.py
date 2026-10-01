from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas.scenario_query import QueryFilter

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]


class TemplateColumn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: Identifier
    title: str = Field(min_length=1, max_length=200)
    operation: Literal["field", "row_number", "sum", "count", "difference", "ratio", "percent"] = "field"
    field: Identifier | None = None
    left: Identifier | None = None
    right: Identifier | None = None
    condition_column: Identifier | None = None
    condition_values: list[str] = Field(default_factory=list, max_length=100)
    display: Literal["text", "number", "percent"] = "text"
    decimals: int = Field(default=2, ge=0, le=8)
    width: int = Field(default=22, ge=6, le=100)
    total: bool = False
    money: bool = False

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation in ("field", "sum") and not self.field:
            raise ValueError("Для поля и суммы укажите field")
        if self.operation in ("difference", "ratio", "percent") and (not self.left or not self.right):
            raise ValueError("Для расчёта выберите два предыдущих показателя")
        if self.operation in ("difference", "sum", "count") and self.display != "number":
            raise ValueError("Для суммы, количества и разности выберите числовой формат")
        if self.operation in ("ratio", "percent") and self.display == "text":
            raise ValueError("Для отношения выберите числовой или процентный формат")
        if bool(self.condition_column) != bool(self.condition_values):
            raise ValueError("Условие требует колонку и список значений")
        if self.condition_column and self.operation not in ("sum", "count"):
            raise ValueError("Условие применимо только к сумме/количеству")
        if self.money and (self.display != "number" or self.operation not in ("field", "sum", "difference")):
            raise ValueError("Денежный формат доступен полю, сумме и разности")
        if self.total and self.display == "percent" and self.operation not in ("ratio", "percent"):
            raise ValueError("Процентный итог должен рассчитываться через отношение показателей")
        if self.total and (self.display == "text" or self.operation == "row_number"):
            raise ValueError("Итог доступен только числовым показателям")
        return self


class TemplateParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: Identifier
    title: str = Field(min_length=1, max_length=200)
    required: bool = True
    default: str | None = Field(default=None, max_length=500)


class ReportTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    table_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$")
    format: Literal["xlsx", "csv"] = "xlsx"
    sheet_name: str = Field(default="Отчёт", min_length=1, max_length=31, pattern=r"^[^\\/*?:\[\]]+$")
    columns: list[TemplateColumn] = Field(min_length=1, max_length=30)
    parameters: list[TemplateParameter] = Field(default_factory=list, max_length=20)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=20)
    group_by: list[Identifier] = Field(default_factory=list, max_length=10)
    distinct: bool = False
    sort_by: Identifier | None = None
    descending: bool = False
    units: Literal["rubles", "thousands", "millions", "billions"] = "rubles"
    freeze_header: bool = True
    autofilter: bool = True
    wrap_text: bool = True
    show_filters: bool = True
    totals: bool = False

    @model_validator(mode="after")
    def validate_structure(self):
        if self.distinct and any(c.operation in ("sum", "count") for c in self.columns):
            raise ValueError("Для агрегатов используйте группировку, а не удаление одинаковых строк")
        previous = {}
        titles = set()
        for column in self.columns:
            if column.key in previous or column.title in titles:
                raise ValueError("Ключи и заголовки колонок не должны повторяться")
            if column.operation in ("difference", "ratio", "percent"):
                for ref in (column.left, column.right):
                    if ref not in previous or previous[ref].display == "text" or previous[ref].operation == "row_number":
                        raise ValueError("Расчёт должен ссылаться на предыдущий числовой показатель")
            previous[column.key] = column
            titles.add(column.title)
        if self.sort_by and self.sort_by not in previous:
            raise ValueError("Неизвестная колонка сортировки")
        if self.sort_by and previous[self.sort_by].operation == "row_number":
            raise ValueError("Сортируйте по данным, нумерация выполняется после сортировки")
        if len({p.column for p in self.parameters}) != len(self.parameters):
            raise ValueError("Параметры не должны повторяться")
        if len(self.parameters) + len(self.filters) > 20:
            raise ValueError("Допускается не более 20 параметров и фильтров")
        return self


def validate_template_sources(template, tables, table_name=None, columns_description=None):
    if template is None:
        return
    def canonical(name):
        return name if "." in name else "public." + name
    sources = [x.model_dump() if hasattr(x, "model_dump") else x for x in tables]
    if not sources and table_name:
        sources = [{"table_name": table_name, "columns_description": columns_description or {}}]
    source = next((x for x in sources if canonical(x["table_name"]) == canonical(template.table_name)), None)
    if source is None:
        raise ValueError("Источник шаблона должен входить в таблицы сценария")
    referenced = set(template.group_by) | {p.column for p in template.parameters} | {f.column for f in template.filters}
    for col in template.columns:
        if col.operation in ("field", "sum") or (col.operation == "count" and col.field):
            referenced.add(col.field)
        if col.condition_column:
            referenced.add(col.condition_column)
    if len(referenced) > 30:
        raise ValueError("Шаблон использует более 30 полей источника")
    if not referenced <= set(source["columns_description"]):
        raise ValueError("Все поля шаблона должны быть описаны в выбранной таблице сценария")


class TemplateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: UUID
    parameters: dict[str, str] = Field(default_factory=dict, max_length=20)
    format: Literal["xlsx", "csv"] | None = None
