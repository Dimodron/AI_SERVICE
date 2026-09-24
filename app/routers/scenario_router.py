from uuid import UUID

from fastapi import APIRouter, Query, Response
from schemas.scenarios import ScenarioCreate, ScenarioResponse, ScenarioUpdate
from services import scenarios

router = APIRouter(prefix="/api/scenarios", tags=["Scenarios"])


@router.post("", response_model=ScenarioResponse, status_code=201)
async def create(payload: ScenarioCreate):
    return await scenarios.create_scenario(payload)


@router.get("", response_model=list[ScenarioResponse])
async def list_all(
    is_active: bool | None = None,
    user_jurpers: int | None = Query(default=None, ge=-(2**63), le=2**63 - 1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await scenarios.list_scenarios(is_active, limit, offset, user_jurpers)


@router.get("/{scenario_id}", response_model=ScenarioResponse)
async def get(scenario_id: UUID):
    return await scenarios.get_scenario(scenario_id)


@router.patch("/{scenario_id}", response_model=ScenarioResponse)
async def update(scenario_id: UUID, payload: ScenarioUpdate):
    return await scenarios.update_scenario(scenario_id, payload)


@router.delete("/{scenario_id}", status_code=204, response_class=Response)
async def delete(scenario_id: UUID):
    await scenarios.delete_scenario(scenario_id)
    return Response(status_code=204)
