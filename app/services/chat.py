from database.history import connect
from fastapi import HTTPException
from schemas.qwen import (
    ChatCreateRequest,
    ChatCreateResponse,
    ChatRequest,
    ChatResponse,
    HistoryRequest,
    HistoryResponse,
)
from services.chat_context import load_chat_context
from services.chat_title import generate_chat_title
from services.files import file_context
from services.QueenModels import resolve_model
from services.scenario_runner import answer_with_scenarios


async def create_chat(payload: ChatCreateRequest) -> ChatCreateResponse:
    model = await resolve_model(payload.model)
    async with await connect() as connection:
        cursor = await connection.execute(
            "INSERT INTO conversations (model) VALUES (%s) "
            "RETURNING id AS conversation_id, model, title, created_at",
            (model,),
        )
        return ChatCreateResponse(**await cursor.fetchone())


async def chat(payload: ChatRequest) -> ChatResponse:
    message = {"role": "user", "content": payload.message}

    if not payload.save_history:
        model = await resolve_model(payload.model)
        async with await connect() as connection:
            system_messages, scenarios = await load_chat_context(connection)
            context = await file_context(connection, payload.file_ids)
            answer = await answer_with_scenarios(
                connection, model, payload, [*system_messages, *context, message], scenarios,
            )
        return ChatResponse(model=model, response=answer)

    async with await connect() as connection:
        await connection.execute("SET LOCAL lock_timeout = '310s'")
        cursor = await connection.execute(
            "SELECT id, model, title FROM conversations WHERE id = %s FOR UPDATE",
            (payload.conversation_id,),
        )
        conversation = await cursor.fetchone()
        if conversation is None:
            raise HTTPException(404, "Диалог не найден")
        model = conversation["model"]
        if payload.model is not None and payload.model != model:
            raise HTTPException(409, "Модель закреплена за диалогом")
        model = await resolve_model(model)

        conversation_id = conversation["id"]
        cursor = await connection.execute(
            "SELECT role, content FROM messages WHERE conversation_id = %s "
            "ORDER BY id DESC LIMIT 20", (conversation_id,)
        )

        history = list(reversed(await cursor.fetchall()))
        context = await file_context(connection, payload.file_ids, conversation_id)
        system_messages, scenarios = await load_chat_context(connection)
        answer = await answer_with_scenarios(
            connection, model, payload, [*system_messages, *context, *history, message], scenarios,
        )
        async with connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)",
                [(conversation_id, "user", payload.message), (conversation_id, "assistant", answer)],
            )
        title = conversation["title"]
        if title is None:
            # Retry an earlier failed title generation using the first exchange.
            cursor = await connection.execute(
                "SELECT content FROM messages WHERE conversation_id = %s ORDER BY id LIMIT 2",
                (conversation_id,),
            )
            first_exchange = await cursor.fetchall()
            title = await generate_chat_title(model, first_exchange[0]["content"], first_exchange[1]["content"])
        await connection.execute(
            "UPDATE conversations SET last_message_at = now(), title = %s WHERE id = %s",
            (title, conversation_id),
        )
    return ChatResponse(conversation_id=conversation_id, title=title, model=model, response=answer)


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