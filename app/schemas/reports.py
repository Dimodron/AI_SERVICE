from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator

from schemas.files import FileResponse

Cell = Annotated[str, Field(max_length=10000)] | StrictBool | StrictInt | StrictFloat | None


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    format: Literal["xlsx", "csv", "docx"]
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=30000, description="Текст или выводы отчёта.")
    columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=30)
    rows: list[list[Cell]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_table(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Число ячеек в каждой строке должно совпадать с columns")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Имена колонок должны быть уникальны")
        if not self.title.strip():
            raise ValueError("Название отчёта не может быть пустым")
        if not self.columns and not self.text.strip():
            raise ValueError("Нужны колонки таблицы или текст отчёта")
        if len(self.model_dump_json()) > 1_000_000:
            raise ValueError("Данные отчёта превышают 1 МБ")
        return self


class ReportToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["xlsx", "csv", "docx"]
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=30000)
    query_result_id: str | None = Field(default=None, description="ID результата query_scenario текущего запроса. Строки подставит сервер.")
    columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=30)
    rows: list[list[Cell]] = Field(default_factory=list, max_length=1000)


class ReportResponse(FileResponse):
    download_url: str
