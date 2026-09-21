from database.history import connect
from fastapi import HTTPException
from schemas.qwen import (
    ChatRequest,
    ChatResponse,
    HistoryRequest,
    HistoryResponse,
)
from services.files import file_context
from services.QueenModels import QwenStrategy, resolve_model


async def chat(payload: ChatRequest) -> ChatResponse:
    message = {"role": "user", "content": payload.message}

    if not payload.save_history:
        model = await resolve_model(payload.model)
        context = []
        if payload.file_ids:
            async with await connect() as connection:
                context = await file_context(connection, payload.file_ids)
        answer = await QwenStrategy(model, payload).chat([*context, message])
        return ChatResponse(model=model, response=answer)

    async with await connect() as connection:
        await connection.execute("SET LOCAL lock_timeout = '310s'")
        if payload.conversation_id:
            cursor = await connection.execute(
                "SELECT id, model FROM conversations WHERE id = %s FOR UPDATE",
                (payload.conversation_id,),
            )
            conversation = await cursor.fetchone()
            if conversation is None:
                raise HTTPException(404, "Диалог не найден")
            model = conversation["model"]
            if payload.model is not None and payload.model != model:
                raise HTTPException(409, "Модель закреплена за диалогом")
            model = await resolve_model(model)
        else:
            model = await resolve_model(payload.model)
            cursor = await connection.execute(
                "INSERT INTO conversations (model) VALUES (%s) RETURNING id, model", (model,)
            )
            conversation = await cursor.fetchone()

        conversation_id = conversation["id"]
        cursor = await connection.execute(
            "SELECT role, content FROM messages WHERE conversation_id = %s "
            "ORDER BY id DESC LIMIT 20", (conversation_id,)
        )

        history = list(reversed(await cursor.fetchall()))
        context = await file_context(connection, payload.file_ids, conversation_id)
        answer = await QwenStrategy(model, payload).chat([*context, *history, message])
        async with connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)",
                [(conversation_id, "user", payload.message), (conversation_id, "assistant", answer)],
            )
    return ChatResponse(conversation_id=conversation_id, model=model, response=answer)


async def history(payload: HistoryRequest) -> dict:
    async with await connect() as connection:
        cursor = await connection.execute(
            "SELECT id FROM conversations WHERE id = %s",
            (payload.conversation_id,),
        )

        if await cursor.fetchone() is None:
            raise HTTPException(404, "Диалог не найден")

        cursor = await connection.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = %s
            ORDER BY id ASC
            LIMIT %s OFFSET %s
            """,
            (
                payload.conversation_id,
                payload.message_count,
                payload.message_last,
            ),
        )

        return HistoryResponse(history=await cursor.fetchall())