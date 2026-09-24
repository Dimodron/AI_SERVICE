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
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (payload.user_login, str(payload.user_jurpers)),
        )
        cursor = await connection.execute(
            "SELECT id FROM users WHERE login = %s AND jurpers = %s ORDER BY created_at, id LIMIT 1",
            (payload.user_login, payload.user_jurpers),
        )
        user = await cursor.fetchone()
        if user is None:
            cursor = await connection.execute(
                "INSERT INTO users (login, jurpers) VALUES (%s, %s) RETURNING id",
                (payload.user_login, payload.user_jurpers),
            )
            user = await cursor.fetchone()
        cursor = await connection.execute(
            "INSERT INTO conversations (model, user_uuid) VALUES (%s, %s) "
            "RETURNING id AS conversation_id, model, title, created_at",
            (model, user["id"]),
        )
        return ChatCreateResponse(**await cursor.fetchone())


async def chat(payload: ChatRequest) -> ChatResponse:
    message = {"role": "user", "content": payload.message}

    if not payload.save_history:
        model = await resolve_model(payload.model)
        async with await connect() as connection:
            system_messages, scenarios = await load_chat_context(connection, payload.user_jurpers)
            context = await file_context(connection, payload.file_ids)
            answer, files = await answer_with_scenarios(
                connection, model, payload, [*system_messages, *context, message], scenarios,
            )
        return ChatResponse(model=model, response=answer, files=files)

    async with await connect() as connection:
        await connection.execute("SET LOCAL lock_timeout = '310s'")
        cursor = await connection.execute(
            "SELECT c.id, c.model, c.title FROM conversations c JOIN users u ON u.id = c.user_uuid "
            "WHERE c.id = %s AND u.login = %s AND u.jurpers = %s FOR UPDATE OF c",
            (payload.conversation_id, payload.user_login, payload.user_jurpers),
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
        system_messages, scenarios = await load_chat_context(connection, payload.user_jurpers)
        answer, files = await answer_with_scenarios(
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
    return ChatResponse(conversation_id=conversation_id, title=title, model=model, response=answer, files=files)


async def history(payload: HistoryRequest) -> dict:
    async with await connect() as connection:
        cursor = await connection.execute(
            "SELECT c.id FROM conversations c JOIN users u ON u.id = c.user_uuid "
            "WHERE c.id = %s AND u.login = %s AND u.jurpers = %s",
            (payload.conversation_id, payload.user_login, payload.user_jurpers),
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

async def list_chats(user_login: str, user_jurpers: int, limit: int, offset: int):
    async with await connect() as connection:
        cursor = await connection.execute(
            "SELECT c.id AS conversation_id, c.model, c.title, c.created_at, c.last_message_at "
            "FROM conversations c JOIN users u ON u.id = c.user_uuid "
            "WHERE u.login = %s AND u.jurpers = %s "
            "ORDER BY COALESCE(c.last_message_at, c.created_at) DESC, c.id DESC LIMIT %s OFFSET %s",
            (user_login, user_jurpers, limit, offset),
        )
        return await cursor.fetchall()


async def delete_chat(conversation_id, user_login: str, user_jurpers: int):
    async with await connect() as connection:
        cursor = await connection.execute(
            "DELETE FROM conversations c USING users u WHERE c.user_uuid = u.id "
            "AND c.id = %s AND u.login = %s AND u.jurpers = %s RETURNING c.id",
            (conversation_id, user_login, user_jurpers),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(404, "Диалог не найден")
