from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from schemas.files import FileResponse
from services.files import get_file, upload_file

router = APIRouter(prefix="/api/files", tags=["Files"])


@router.post("", response_model=FileResponse, status_code=201)
async def upload(file: UploadFile):
    try:
        return await upload_file(file)
    finally:
        await file.close()


@router.get("/{file_id}", response_model=FileResponse)
async def metadata(file_id: UUID):
    return await get_file(file_id)


@router.get("/{file_id}/download")
async def download(file_id: UUID):
    record = await get_file(file_id, include_content=True)
    return Response(
        content=bytes(record["content"]),
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(record["filename"], safe=""),
            # Original text files can be Windows-1251: don't label them UTF-8.
            "Content-Type": record["media_type"],
        },
    )
