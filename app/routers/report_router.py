from fastapi import APIRouter

from database.history import connect
from schemas.reports import ReportCreate, ReportResponse
from services.reports import create_report

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.post("", response_model=ReportResponse, status_code=201)
async def create(payload: ReportCreate):
    async with await connect() as connection:
        return await create_report(connection, payload)
