from uuid import UUID

from fastapi import APIRouter, Query, Response
from schemas.prompts import PromptCreate, PromptResponse, PromptUpdate
from services import prompts

router = APIRouter(prefix="/api/system_prompt", tags=["System prompts"])


@router.post("", response_model=PromptResponse, status_code=201)
async def create(payload: PromptCreate):
    return await prompts.create_prompt(payload)


@router.get("", response_model=list[PromptResponse])
async def list_all(
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await prompts.list_prompts(is_active, limit, offset)


@router.get("/{prompt_id}", response_model=PromptResponse)
async def get(prompt_id: UUID):
    return await prompts.get_prompt(prompt_id)


@router.patch("/{prompt_id}", response_model=PromptResponse)
async def update(prompt_id: UUID, payload: PromptUpdate):
    return await prompts.update_prompt(prompt_id, payload)


@router.delete("/{prompt_id}", status_code=204, response_class=Response)
async def delete(prompt_id: UUID):
    await prompts.delete_prompt(prompt_id)
    return Response(status_code=204)
