"""Internal model protocol; the public chat response remains plain text."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    status: Literal["processing", "completed", "needs_clarification"]
    response: str = Field(min_length=1)
