import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")


class ImportRequest(BaseModel):
    tables: list[str] = Field(min_length=1, max_length=20)
    filters: dict[str, str] = Field(default_factory=dict)
    mode: Literal["replace", "append"] = "replace"

    @model_validator(mode="after")
    def validate_names(self):
        names = self.tables + list(self.filters)
        if any(not IDENTIFIER.fullmatch(name) for name in names):
            raise ValueError("Имена таблиц и колонок: латинские буквы, цифры, _, до 63 символов")
        if len({name.lower() for name in self.tables}) != len(self.tables):
            raise ValueError("Таблицы не должны повторяться")
        return self
