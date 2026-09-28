"""Identity asserted by the Oracle server, authenticated with CHAT_API_TOKEN."""
from dataclasses import dataclass
from fastapi import Depends, Header, HTTPException, Request
from database.history import connect
from services.user_context import trusted_chat_source


@dataclass(frozen=True)
class OracleIdentity:
    login: str
    is_admin: bool


async def oracle_identity(request: Request,
                          x_user_login: str | None = Header(default=None),
                          x_user_admin: str | None = Header(default=None),
                          trusted: bool = Depends(trusted_chat_source)) -> OracleIdentity | None:
    if x_user_login is None and x_user_admin is None:
        return None  # Compatibility for existing API integrations.
    if not trusted:
        raise HTTPException(403, 'Для передачи роли Oracle требуется токен сервиса')
    if not x_user_login or len(x_user_login) > 200 or x_user_admin not in ('0', '1'):
        raise HTTPException(422, 'Некорректная серверная идентификация Oracle')
    # A browser cannot select another account through the proxied body/query.
    supplied = request.query_params.get('user_login')
    if request.method in ('POST', 'PATCH') and request.headers.get('content-type', '').startswith('application/json'):
        try:
            body = await request.json()
        except ValueError as error:
            raise HTTPException(422, 'Некорректный JSON запроса') from error
        if isinstance(body, dict):
            supplied = body.get('user_login', supplied)
    if supplied is not None and supplied != x_user_login:
        raise HTTPException(403, 'Логин запроса не совпадает с сессией Oracle')
    actor = OracleIdentity(x_user_login, x_user_admin == '1')
    async with await connect() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('chat-owner'), hashtext(%s))", (actor.login,))
        cursor = await conn.execute('UPDATE users SET is_admin=%s WHERE login=%s RETURNING id', (actor.is_admin, actor.login))
        if await cursor.fetchone() is None:
            await conn.execute('INSERT INTO users(login,is_admin) VALUES (%s,%s)', (actor.login,actor.is_admin))
    return actor


async def require_admin(actor: OracleIdentity | None = Depends(oracle_identity)) -> OracleIdentity:
    if actor is None or not actor.is_admin:
        raise HTTPException(403, 'Доступно только администратору')
    return actor
