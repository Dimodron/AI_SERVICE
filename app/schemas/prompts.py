from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class PromptCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(min_length=1, description="Текст системного промпта.")
    order_num: int = Field(default=0, strict=True, ge=-(2**31), le=2**31 - 1)
    is_active: StrictBool = True
    create_user: UUID | None = Field(default=None, description="ID автора из таблицы users.")


class PromptUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str | None = Field(default=None, min_length=1)
    order_num: int | None = Field(default=None, strict=True, ge=-(2**31), le=2**31 - 1)
    is_active: StrictBool | None = None
    edit_user: UUID | None = Field(default=None, description="ID редактора из таблицы users.")

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("Укажите хотя бы одно поле для изменения")
        for field in ("prompt", "order_num", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} не может быть null")
        return self


class PromptResponse(BaseModel):
    id: UUID
    order_num: int
    prompt: str
    is_active: bool
    create_user: UUID | None
    edit_user: UUID | None
    create_time: datetime
    edit_time: datetime
