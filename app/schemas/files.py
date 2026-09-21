from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class FileResponse(BaseModel):
    file_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    created_at: datetime
