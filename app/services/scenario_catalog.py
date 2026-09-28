"""Read-only metadata for tables supported by query_scenario."""
from fastapi import HTTPException
from database.history import connect
from services.scenario_runner import _INTERNAL_TABLES

# The scenario query language accepts ordinary unquoted identifier characters.
_NAME = r'^[a-zA-Z_][a-zA-Z0-9_]{0,62}$'
_VISIBLE = """
    n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    AND n.nspname ~ %s AND c.relname ~ %s
    AND c.relkind IN ('r', 'p')
    AND (%s OR NOT (c.relname = ANY(%s)))
    AND has_schema_privilege(n.oid, 'USAGE')
    AND has_table_privilege(c.oid, 'SELECT')
"""


async def schemas(include_system: bool = False):
    async with await connect() as conn:
        cur = await conn.execute(
            'SELECT DISTINCT n.nspname AS name FROM pg_catalog.pg_namespace n '
            'JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid WHERE ' + _VISIBLE + ' ORDER BY name',
            (_NAME, _NAME, include_system, sorted(_INTERNAL_TABLES)),
        )
        return [row['name'] for row in await cur.fetchall()]


async def tables(schema: str, include_system: bool = False):
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT c.relname AS name, coalesce(pg_catalog.obj_description(c.oid, 'pg_class'), '') AS description "
            'FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
            'WHERE ' + _VISIBLE + ' AND n.nspname=%s ORDER BY c.relname',
            (_NAME, _NAME, include_system, sorted(_INTERNAL_TABLES), schema),
        )
        return await cur.fetchall()


async def columns(schema: str, table: str, include_system: bool = False):
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT c.oid, coalesce(pg_catalog.obj_description(c.oid, 'pg_class'), '') AS description "
            'FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
            'WHERE ' + _VISIBLE + ' AND n.nspname=%s AND c.relname=%s',
            (_NAME, _NAME, include_system, sorted(_INTERNAL_TABLES), schema, table),
        )
        relation = await cur.fetchone()
        if relation is None:
            raise HTTPException(404, 'Таблица отсутствует или недоступна для сценария')
        cur = await conn.execute(
            "SELECT a.attname AS name, pg_catalog.format_type(a.atttypid,a.atttypmod) AS data_type, "
            "NOT a.attnotnull AS nullable, coalesce(pg_catalog.col_description(a.attrelid,a.attnum), '') AS description "
            'FROM pg_catalog.pg_attribute a WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum',
            (relation['oid'],),
        )
        return {'table_name': schema + '.' + table, 'description': relation['description'], 'columns': await cur.fetchall()}
