from uuid import UUID

from fastapi import HTTPException
from psycopg import sql
from psycopg.errors import ForeignKeyViolation

from database.history import connect
from schemas.prompts import PromptCreate, PromptUpdate

_COLUMNS = "id, order_num, prompt, is_active, create_user, edit_user, create_time, edit_time"


def _require_prompt(record):
    if record is None:
        raise HTTPException(404, "Промпт не найден")
    return record


async def create_prompt(payload: PromptCreate):
    try:
        async with await connect() as connection:
            cursor = await connection.execute(
                f"INSERT INTO system_prompt (prompt, order_num, is_active, create_user) "
                f"VALUES (%s, %s, %s, %s) RETURNING {_COLUMNS}",
                (payload.prompt, payload.order_num, payload.is_active, payload.create_user),
            )
            return await cursor.fetchone()
    except ForeignKeyViolation as error:
        raise HTTPException(422, "Пользователь create_user не найден") from error


async def list_prompts(is_active: bool | None, limit: int, offset: int):
    async with await connect() as connection:
        cursor = await connection.execute(
            f"SELECT {_COLUMNS} FROM system_prompt "
            "WHERE (%s::boolean IS NULL OR is_active = %s) "
            "ORDER BY order_num, create_time, id LIMIT %s OFFSET %s",
            (is_active, is_active, limit, offset),
        )
        return await cursor.fetchall()


async def get_prompt(prompt_id: UUID):
    async with await connect() as connection:
        cursor = await connection.execute(
            f"SELECT {_COLUMNS} FROM system_prompt WHERE id = %s", (prompt_id,),
        )
        return _require_prompt(await cursor.fetchone())


async def update_prompt(prompt_id: UUID, payload: PromptUpdate):
    changes = payload.model_dump(exclude_unset=True)
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(field)) for field in changes
    )
    query = sql.SQL(
        "UPDATE system_prompt SET {}, edit_time = now() WHERE id = %s RETURNING " + _COLUMNS
    ).format(assignments)
    try:
        async with await connect() as connection:
            cursor = await connection.execute(query, (*changes.values(), prompt_id))
            return _require_prompt(await cursor.fetchone())
    except ForeignKeyViolation as error:
        raise HTTPException(422, "Пользователь edit_user не найден") from error


async def delete_prompt(prompt_id: UUID):
    async with await connect() as connection:
        cursor = await connection.execute(
            "DELETE FROM system_prompt WHERE id = %s RETURNING id", (prompt_id,),
        )
        _require_prompt(await cursor.fetchone())
