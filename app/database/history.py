from contextlib import asynccontextmanager
from config import settings

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None


@asynccontextmanager
async def database_lifespan():
    global _pool
    pool = AsyncConnectionPool(
        min_size=0,
        max_size=settings.PG_POOL_SIZE,
        max_waiting=settings.PG_POOL_MAX_WAITING,
        timeout=settings.PG_POOL_TIMEOUT,
        kwargs={"connect_timeout": settings.PG_CONNECT_TIMEOUT, "row_factory": dict_row},
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
