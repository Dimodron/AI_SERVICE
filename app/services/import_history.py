from database.history import connect
from psycopg.types.json import Jsonb


async def record(conn, request, started_at, results=None, error=None):
    counts = {item["table"]: item["rows"] for item in results or []}
    for name in request.tables:
        table = "oracle_data." + name.lower()
        await conn.execute(
            "INSERT INTO public.data_import_history "
            "(table_name,mode,filters,started_at,status,row_count,error) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (table, request.mode, Jsonb(request.filters), started_at,
             "error" if error is not None else "success", counts.get(table), error),
        )


async def history(table=None, limit=50, offset=0):
    name = "oracle_data." + table.lower() if table else None
    async with await connect() as conn:
        cursor = await conn.execute(
            "SELECT id,table_name,mode,filters,started_at,finished_at,status,row_count,error "
            "FROM public.data_import_history WHERE (%s::text IS NULL OR table_name=%s) "
            "ORDER BY finished_at DESC,id DESC LIMIT %s OFFSET %s",
            (name, name, limit + 1, offset),
        )
        rows = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT max(finished_at) AS last_success_at FROM public.data_import_history "
            "WHERE status='success' AND (%s::text IS NULL OR table_name=%s)", (name, name),
        )
        last = await cursor.fetchone()
        return {"items": rows[:limit], "has_more": len(rows) > limit, **last}
