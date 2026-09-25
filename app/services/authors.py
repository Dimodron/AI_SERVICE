from fastapi import HTTPException


async def author_id(connection, login: str | None):
    """Resolve a public author login to the existing internal user key."""
    if login is None:
        return None
    cursor = await connection.execute(
        'SELECT id FROM users WHERE login = %s ORDER BY created_at, id LIMIT 1',
        (login,),
    )
    user = await cursor.fetchone()
    if user is None:
        raise HTTPException(422, 'Пользователь с указанным логином не найден в users')
    return user['id']
