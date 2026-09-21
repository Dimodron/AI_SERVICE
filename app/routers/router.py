from fastapi import APIRouter
from schemas.qwen import (
    ChatRequest,
    ChatResponse,
    GenerateRequest,
    GenerateResponse,
    HistoryRequest,
    HistoryResponse,
)
from services.chat import chat as chat_service
from services.chat import history as history_service
from services.QueenModels import ask_qwen, list_models

router = APIRouter(prefix="/api", tags=["Qwen"])


@router.get("/models", response_model=list[str])
async def models():
    return await list_models()


@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest):
    answer = await ask_qwen(payload.prompt, payload.model, payload)
    return GenerateResponse(response=answer)


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    return await chat_service(payload)


@router.post("/chat/history", response_model=HistoryResponse)
async def history(payload: HistoryRequest) -> HistoryResponse:
    return await history_service(payload)