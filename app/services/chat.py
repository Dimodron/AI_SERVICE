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
from services.user_context import user_context
from services.chat_context import load_chat_context
from services.chat_title import generate_chat_title
from services.files import file_context
from services.QueenModels import QwenStrategy, resolve_model
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


def model_messages(system_messages, history, context, question):
    """Keep attachment data and question in the same user turn for model templates."""
    instructions = [item["content"] for item in system_messages]
    instructions.extend(item["content"] for item in context if item["role"] == "system")
    attachments = [item for item in context if item["role"] == "user"]
    current = {"role": "user", "content": question}
    if attachments:
        current["content"] = "\n\n".join(item["content"] for item in attachments) + "\n\nВопрос пользователя:\n" + question
        images = [image for item in attachments for image in item.get("images", [])]
        if images:
            current["images"] = images
    leading = [{"role": "system", "content": "\n\n".join(instructions)}] if instructions else []
    return [*leading, *history, current]


async def chat(payload: ChatRequest, *, trusted_source=False) -> ChatResponse:
    if not payload.save_history:
        model = await resolve_model(payload.model)
        async with await connect() as connection:
            profile, is_admin = await user_context(connection, payload, trusted_source)
            system_messages, scenarios = await load_chat_context(connection, payload.user_jurpers, is_admin=is_admin)
            system_messages.append(profile)
            context = await file_context(connection, payload.file_ids)
            answer, files = await answer_with_scenarios(
                connection, model, payload, model_messages(system_messages, [], context, payload.message), scenarios, is_admin=is_admin,
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
            raise HTTPException(409, "Модель диалога отличается от запроса; переключите её через PATCH /api/chat/{id}/model")
        model = await resolve_model(model)

        conversation_id = conversation["id"]
        cursor = await connection.execute(
            "SELECT role, content FROM messages WHERE conversation_id = %s AND content <> '' "
            "ORDER BY id DESC LIMIT 20", (conversation_id,)
        )

        history = list(reversed(await cursor.fetchall()))
        context = await file_context(connection, payload.file_ids, conversation_id)
        profile, is_admin = await user_context(connection, payload, trusted_source)
        system_messages, scenarios = await load_chat_context(connection, payload.user_jurpers, is_admin=is_admin)
        system_messages.append(profile)
        answer, files = await answer_with_scenarios(
            connection, model, payload, model_messages(system_messages, history, context, payload.message), scenarios, is_admin=is_admin,
        )
        await save_message(connection, conversation_id, "user", payload.message, payload.file_ids)
        await save_message(connection, conversation_id, "assistant", answer, [file.file_id for file in files])
        title = conversation["title"]
        if title is None:
            # Retry an earlier failed title generation using the first exchange.
            cursor = await connection.execute(
                "SELECT content FROM messages WHERE conversation_id = %s AND content <> '' ORDER BY id LIMIT 2",
                (conversation_id,),
            )
            first_exchange = await cursor.fetchall()
            title = await generate_chat_title(model, first_exchange[0]["content"], first_exchange[1]["content"])
        await connection.execute(
            "UPDATE conversations SET last_message_at = now(), title = %s WHERE id = %s",
            (title, conversation_id),
        )
    return ChatResponse(conversation_id=conversation_id, title=title, model=model, response=answer, files=files)


async def history(payload: HistoryRequest) -> HistoryResponse:
    async with await connect() as connection:
        cursor = await connection.execute(
            "SELECT c.id, c.model FROM conversations c JOIN users u ON u.id = c.user_uuid "
            "WHERE c.id = %s AND u.login = %s AND u.jurpers = %s",
            (payload.conversation_id, payload.user_login, payload.user_jurpers),
        )

        conversation = await cursor.fetchone()
        if conversation is None:
            raise HTTPException(404, "Диалог не найден")

        cursor = await connection.execute(
            """
            SELECT id, role, content
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

        rows = await cursor.fetchall()
        for row in rows:
            files_cursor = await connection.execute(
                "SELECT f.id AS file_id, f.filename, f.media_type, f.size_bytes, f.created_at "
                "FROM message_files mf JOIN files f ON f.id=mf.file_id WHERE mf.message_id=%s ORDER BY f.created_at, f.id",
                (row.pop("id"),),
            )
            row["files"] = await files_cursor.fetchall()
        return HistoryResponse(model=conversation["model"], history=rows, files=await conversation_attachments(connection, payload.conversation_id))

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


async def conversation_attachments(connection, conversation_id):
    cursor = await connection.execute(
        "SELECT f.id AS file_id, f.filename, f.media_type, f.size_bytes, f.created_at "
        "FROM conversation_files cf JOIN files f ON f.id=cf.file_id "
        "WHERE cf.conversation_id=%s ORDER BY f.created_at, f.id", (conversation_id,))
    return await cursor.fetchall()


async def save_message(connection, conversation_id, role, content, file_ids):
    cursor = await connection.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s) RETURNING id",
        (conversation_id, role, content),)
    message_id = (await cursor.fetchone())["id"]
    for file_id in dict.fromkeys(file_ids):
        await connection.execute("INSERT INTO message_files (message_id,file_id) VALUES (%s,%s)", (message_id, file_id))


async def attach_files(conversation_id, payload):
    async with await connect() as connection:
        cursor = await connection.execute(
            "SELECT c.id, c.model FROM conversations c JOIN users u ON u.id=c.user_uuid "
            "WHERE c.id=%s AND u.login=%s AND u.jurpers=%s FOR UPDATE OF c",
            (conversation_id, payload.user_login, payload.user_jurpers),)
        conversation = await cursor.fetchone()
        if conversation is None:
            raise HTTPException(404, "Диалог не найден")
        existing = {row["file_id"] for row in await conversation_attachments(connection, conversation_id)}
        new_ids = [file_id for file_id in dict.fromkeys(payload.file_ids) if file_id not in existing]
        context = await file_context(connection, payload.file_ids, conversation_id)
        if any(item.get("images") for item in context):
            await QwenStrategy(conversation["model"]).ensure_vision()
        if new_ids:
            await save_message(connection, conversation_id, "user", "", new_ids)
            await connection.execute("UPDATE conversations SET last_message_at=now() WHERE id=%s", (conversation_id,))
        return {"conversation_id": conversation_id, "files": await conversation_attachments(connection, conversation_id)}


async def change_chat_model(conversation_id, payload):
    async with await connect() as connection:
        await connection.execute("SET LOCAL lock_timeout = '10s'")
        cursor = await connection.execute(
            "SELECT c.id FROM conversations c JOIN users u ON u.id=c.user_uuid "
            "WHERE c.id=%s AND u.login=%s AND u.jurpers=%s FOR UPDATE OF c",
            (conversation_id, payload.user_login, payload.user_jurpers),)
        if await cursor.fetchone() is None:
            raise HTTPException(404, "Диалог не найден")
        model = await resolve_model(payload.model)
        cursor = await connection.execute(
            "SELECT 1 FROM conversation_files cf JOIN files f ON f.id=cf.file_id "
            "WHERE cf.conversation_id=%s AND f.media_type IN ('image/png','image/jpeg') LIMIT 1",
            (conversation_id,),)
        if await cursor.fetchone() is not None:
            await QwenStrategy(model).ensure_vision()
        cursor = await connection.execute(
            "UPDATE conversations SET model=%s WHERE id=%s "
            "RETURNING id AS conversation_id, model, title, created_at, last_message_at",
            (model, conversation_id),)
        return ChatCreateResponse(**await cursor.fetchone())
