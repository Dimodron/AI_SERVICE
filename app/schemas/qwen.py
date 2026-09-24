from datetime import datetime
from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, model_validator


class ModelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    num_ctx: int | None = Field(
        default=None, gt=0, strict=True, examples=[8192],
        description="Размер контекста в токенах. Больший контекст требует больше RAM/VRAM. При null используются настройки Ollama.",
    )


class GenerationSettings(BaseModel):
    think: StrictBool | Literal["low", "medium", "high", "max"] | None = Field(
        default=None, examples=[False],
        description="Включить (true) или выключить (false) рассуждения. Некоторые модели принимают уровни low/medium/high/max. Поддержка зависит от модели; null оставляет настройки Ollama. Возвращается только конечный ответ.",
    )
    options: ModelOptions | None = Field(
        default=None, description="Параметры генерации на текущий запрос. В диалоге не сохраняются.",
    )
    keep_alive: Annotated[str, Field(min_length=1)] | Annotated[int, Field(strict=True)] | None = Field(
        default=None, examples=["5m", 0],
        description="Как долго держать модель в памяти после запроса: строка длительности (5m), число секунд, 0 — выгрузить, -1 — оставить загруженной. Если не задано или null, используется 30m.",
    )


class GenerateRequest(GenerationSettings):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=1, description="Текст запроса к модели.", examples=["Объясни, как работает Redis"])
    model: str | None = Field(default=None, min_length=1, description="Имя модели из GET /api/models. Если не задано, выбирается первая модель в списке Ollama.", examples=["qwen3.5:4b"])


class GenerateResponse(BaseModel):
    response: str = Field(description="Конечный ответ модели без текста рассуждений.")


class ChatCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model: str | None = Field(default=None, min_length=1, description="Модель диалога. По умолчанию первая доступная модель Ollama.")


class ChatCreateResponse(BaseModel):
    conversation_id: UUID
    model: str
    title: str | None
    created_at: datetime


class ChatRequest(GenerationSettings):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message: str = Field(min_length=1, max_length=8000, description="Сообщение пользователя или вопрос по прикреплённым файлам.", examples=["Опиши что на картинке"])
    file_ids: list[UUID] = Field(default_factory=list, max_length=5, description="До 5 идентификаторов файлов, полученных через POST /api/files. Для изображений нужна модель с поддержкой vision. Чтение файлов использует БД даже при save_history=false.")
    save_history: bool = Field(default=False, description="Сохранять сообщения и привязки файлов в БД. При true обязателен conversation_id, полученный через POST /api/chat/create.")
    conversation_id: UUID | None = Field(default=None, description="ID диалога, созданного через POST /api/chat/create. Требует save_history=true; используются его история, файлы и закреплённая модель.")
    model: str | None = Field(default=None, min_length=1, description="Имя модели из GET /api/models. Для нового запроса по умолчанию берётся первая модель Ollama; для существующего диалога — сохранённая. Менять модель диалога нельзя.", examples=["qwen3.5:4b"])
    user_login: str = Field(min_length=1, description="Логин пользователя.", examples=["ivan"])
    user_jurpers: int = Field(strict=True, ge=-(2**63), le=2**63 - 1, description="Идентификатор jurpers пользователя (BIGINT).", examples=[123])
    user_organization: int | None = Field(default=None, strict=True, ge=-(2**63), le=2**63 - 1, description="Необязательный фильтр organization для данных сценариев. Применяется вместе с user_jurpers.", examples=[42])
    user_info: dict[str, JsonValue] = Field(default_factory=dict, description="Дополнительная информация о пользователе в виде JSON-объекта.", examples=[{"name": "Иван", "organization": 42}])

    @model_validator(mode="after")
    def validate_history(self):
        if self.save_history and self.conversation_id is None:
            raise ValueError("Сначала создайте диалог через POST /api/chat/create и передайте conversation_id")
        if self.conversation_id and not self.save_history:
            raise ValueError("conversation_id требует save_history=true")
        return self

class ChatResponse(BaseModel):
    title: str | None = Field(default=None, description="Название диалога, автоматически созданное моделью; null до генерации или без сохранения истории.")
    conversation_id: UUID | None = Field(default=None, description="ID диалога; null, если история не сохраняется.")
    model: str = Field(description="Имя модели, сформировавшей ответ.")
    response: str = Field(description="Конечный ответ модели без текста рассуждений.")


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message_count: int = Field(default=20, ge=1, le=100)
    message_last: int = Field(default=0, ge=0)

class HistoryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class HistoryResponse(BaseModel):
    history: list[HistoryMessage]