from fastapi import APIRouter
from schemas.qwen import (
    ChatCreateRequest,
    ChatCreateResponse,
    ChatRequest,
    ChatResponse,
    HistoryRequest,
    HistoryResponse,
)
from services.chat import chat as chat_service
from services.chat import create_chat as create_chat_service
from services.chat import history as history_service
from services.QueenModels import list_models

router = APIRouter(prefix="/api", tags=["Qwen"])


@router.get("/models", response_model=list[str])
async def models():
    return await list_models()


@router.post("/chat/create", response_model=ChatCreateResponse, status_code=201)
async def create_chat(payload: ChatCreateRequest):
    return await create_chat_service(payload)


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    return await chat_service(payload)


@router.post("/chat/history", response_model=HistoryResponse)
async def history(payload: HistoryRequest) -> HistoryResponse:
    return await history_service(payload)
