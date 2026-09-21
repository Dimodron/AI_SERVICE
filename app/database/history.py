from contextlib import asynccontextmanager
from os import environ

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None


@asynccontextmanager
async def database_lifespan():
    global _pool
    pool = AsyncConnectionPool(
        min_size=0,
        max_size=int(environ.get("PG_POOL_SIZE", "10")),
        max_waiting=50,
        timeout=10,
        kwargs={"connect_timeout": 10, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    await pool.open()
    _pool = pool
    try:
        yield
    finally:
        _pool = None
        await pool.close()


async def connect():
    if _pool is None:
        raise RuntimeError("PostgreSQL pool is not initialized: start the application lifespan")
    return _pool.connection()
