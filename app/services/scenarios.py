from uuid import UUID

from database.history import connect
from fastapi import HTTPException
from psycopg import sql
from psycopg.errors import ForeignKeyViolation
from psycopg.types.json import Jsonb
from schemas.scenarios import ScenarioCreate, ScenarioUpdate

_COLUMNS = "id, title, description, table_name, columns_description, scenario, visible_jurpers, is_active, create_user, edit_user, create_time, edit_time"


def _require_scenario(record):
    if record is None:
        raise HTTPException(404, "Сценарий не найден")
    return record


async def create_scenario(payload: ScenarioCreate):
    try:
        async with await connect() as connection:
            cursor = await connection.execute(
                f"INSERT INTO scenarios (title, description, table_name, columns_description, scenario, visible_jurpers, is_active, create_user) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (payload.title, payload.description, payload.table_name, Jsonb(payload.columns_description),
                 payload.scenario, payload.visible_jurpers, payload.is_active, payload.create_user),
            )
            return await cursor.fetchone()
    except ForeignKeyViolation as error:
        raise HTTPException(422, "Пользователь create_user не найден") from error


async def list_scenarios(is_active: bool | None, limit: int, offset: int, user_jurpers: int | None = None):
    async with await connect() as connection:
        cursor = await connection.execute(
            f"SELECT {_COLUMNS} FROM scenarios "
            "WHERE (%s::boolean IS NULL OR is_active = %s) "
            "AND (%s::bigint IS NULL OR cardinality(visible_jurpers) = 0 OR %s = ANY(visible_jurpers)) "
            "ORDER BY create_time, id LIMIT %s OFFSET %s",
            (is_active, is_active, user_jurpers, user_jurpers, limit, offset),
        )
        return await cursor.fetchall()


async def get_scenario(scenario_id: UUID):
    async with await connect() as connection:
        cursor = await connection.execute(
            f"SELECT {_COLUMNS} FROM scenarios WHERE id = %s", (scenario_id,),
        )
        return _require_scenario(await cursor.fetchone())


async def update_scenario(scenario_id: UUID, payload: ScenarioUpdate):
    changes = payload.model_dump(exclude_unset=True)
    if "columns_description" in changes:
        changes["columns_description"] = Jsonb(changes["columns_description"])
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(field)) for field in changes
    )
    query = sql.SQL(
        "UPDATE scenarios SET {}, edit_time = now() WHERE id = %s RETURNING " + _COLUMNS
    ).format(assignments)
    try:
        async with await connect() as connection:
            cursor = await connection.execute(query, (*changes.values(), scenario_id))
            return _require_scenario(await cursor.fetchone())
    except ForeignKeyViolation as error:
        raise HTTPException(422, "Пользователь edit_user не найден") from error


async def delete_scenario(scenario_id: UUID):
    async with await connect() as connection:
        cursor = await connection.execute(
            "DELETE FROM scenarios WHERE id = %s RETURNING id", (scenario_id,),
        )
        _require_scenario(await cursor.fetchone())
