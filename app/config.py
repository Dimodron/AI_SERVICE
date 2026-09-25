"""Validated process-wide settings, loaded once at application startup."""
from os import environ
from pathlib import Path
import tomllib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.toml"
# Compatibility with previously deployed .env files. Empty values mean TOML.
ENV_OVERRIDES = {"MAX_FILE_BYTES", "MAX_FILES", "PG_POOL_SIZE", "QWEN_URL", "DATA_IMPORT_TIMEOUT"}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    MAX_FILE_BYTES: PositiveInt
    MAX_FILES: PositiveInt
    MAX_TEXT_CHARS: PositiveInt
    MAX_IMAGE_PIXELS: PositiveInt
    MAX_EXCEL_UNPACKED_BYTES: PositiveInt
    MAX_EXCEL_ROWS: PositiveInt
    MAX_EXCEL_COLUMNS: PositiveInt
    QWEN_URL: str = Field(min_length=1)
    QWEN_CONNECT_TIMEOUT: PositiveInt
    QWEN_READ_TIMEOUT: PositiveInt
    QWEN_WRITE_TIMEOUT: PositiveInt
    QWEN_POOL_TIMEOUT: PositiveInt
    QWEN_METADATA_TIMEOUT: PositiveInt
    QWEN_MAX_CONNECTIONS: PositiveInt
    QWEN_KEEPALIVE_CONNECTIONS: PositiveInt
    CHAT_TITLE_TIMEOUT: PositiveInt
    PG_POOL_SIZE: PositiveInt
    PG_POOL_MAX_WAITING: PositiveInt
    PG_POOL_TIMEOUT: PositiveInt
    PG_CONNECT_TIMEOUT: PositiveInt
    MAX_TOOL_CALLS: PositiveInt
    MAX_RESULT_CHARS: PositiveInt
    DATA_IMPORT_TIMEOUT: PositiveInt
    USER_CONTEXT_TABLE: str = "oracle_data.gpt_user_context"
    ADMIN_IGNORE_SYSTEM_PROMPTS: bool = True

    @model_validator(mode="after")
    def validate_connections(self):
        if self.QWEN_KEEPALIVE_CONNECTIONS > self.QWEN_MAX_CONNECTIONS:
            raise ValueError("QWEN_KEEPALIVE_CONNECTIONS не может превышать QWEN_MAX_CONNECTIONS")
        if not self.QWEN_URL.startswith(("http://", "https://")):
            raise ValueError("QWEN_URL должен начинаться с http:// или https://")
        import re
        if self.USER_CONTEXT_TABLE and not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}\.[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.USER_CONTEXT_TABLE):
            raise ValueError("USER_CONTEXT_TABLE: ожидается schema.table либо пустая строка")
        return self


def load_settings(path: Path | None = None, overrides=None) -> Settings:
    source = path if path is not None else Path(environ.get("SETTINGS_FILE") or DEFAULT_SETTINGS_PATH)
    with source.open("rb") as stream:
        values = tomllib.load(stream)
    environment = environ if overrides is None else overrides
    for name in ENV_OVERRIDES:
        if value := environment.get(name):
            try:
                values[name] = value if name == "QWEN_URL" else int(value)
            except ValueError:
                raise ValueError(f"{name}: ожидается положительное целое число") from None
    return Settings.model_validate(values)


settings = load_settings()
