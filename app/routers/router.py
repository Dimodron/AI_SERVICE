from uuid import UUID
from fastapi import APIRouter, Query, Response
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
from services.chat import list_chats, delete_chat
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


@router.get("/chats", response_model=list[ChatCreateResponse])
async def chats(
    user_login: str = Query(min_length=1, max_length=200),
    user_jurpers: int = Query(ge=-(2**63), le=2**63 - 1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await list_chats(user_login, user_jurpers, limit, offset)


@router.delete("/chat/{conversation_id}", status_code=204, response_class=Response)
async def remove_chat(
    conversation_id: UUID,
    user_login: str = Query(min_length=1, max_length=200),
    user_jurpers: int = Query(ge=-(2**63), le=2**63 - 1),
):
    await delete_chat(conversation_id, user_login, user_jurpers)
    return Response(status_code=204)
