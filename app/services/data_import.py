from config import settings

import asyncio
import logging
from decimal import Decimal
from os import environ

import oracledb
from database.history import connect
from fastapi import HTTPException
from psycopg import sql
from psycopg.types.json import Jsonb
from schemas.data_import import IDENTIFIER, ImportRequest

logger = logging.getLogger(__name__)
SCHEMA = "oracle_data"


def column_type(info):
    kind = info.type_code
    if kind == oracledb.DB_TYPE_NUMBER:
        return "numeric"
    if kind in (oracledb.DB_TYPE_BINARY_FLOAT, oracledb.DB_TYPE_BINARY_DOUBLE):
        return "double precision"
    if kind in (oracledb.DB_TYPE_DATE, oracledb.DB_TYPE_TIMESTAMP):
        return "timestamp without time zone"
    if kind in (oracledb.DB_TYPE_TIMESTAMP_TZ, oracledb.DB_TYPE_TIMESTAMP_LTZ):
        return "timestamp with time zone"
    if kind in (oracledb.DB_TYPE_RAW, oracledb.DB_TYPE_LONG_RAW, oracledb.DB_TYPE_BLOB):
        return "bytea"
    if kind == oracledb.DB_TYPE_BOOLEAN:
        return "boolean"
    if kind == oracledb.DB_TYPE_JSON:
        return "jsonb"
    if kind in (oracledb.DB_TYPE_VARCHAR, oracledb.DB_TYPE_NVARCHAR,
                oracledb.DB_TYPE_CHAR, oracledb.DB_TYPE_NCHAR,
                oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB, oracledb.DB_TYPE_LONG):
        return "text"
    raise HTTPException(422, f"Неподдерживаемый тип колонки {info.name}: {kind}")


def output_type_handler(cursor, metadata):
    if metadata.type_code == oracledb.DB_TYPE_NUMBER:
        return cursor.var(oracledb.DB_TYPE_VARCHAR, arraysize=cursor.arraysize,
                          outconverter=Decimal)


async def duplicate_tables(request: ImportRequest):
    required = ("ORACLE_HOST", "ORACLE_USER", "ORACLE_PASS", "ORACLE_NAME")
    if any(not environ.get(key) for key in required):
        raise HTTPException(503, "Не настроено подключение к Oracle")
    owner = environ.get("ORACLE_SCHEMA", "APXWS").upper()
    if not IDENTIFIER.fullmatch(owner):
        raise HTTPException(503, "Некорректная настройка ORACLE_SCHEMA")
    try:
        timeout = settings.DATA_IMPORT_TIMEOUT
        port = int(environ.get("ORACLE_PORT", "1521"))
        if timeout <= 0:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "Некорректные настройки импорта") from None
    results = []
    try:
        async with asyncio.timeout(timeout):
            async with oracledb.connect_async(
                user=environ["ORACLE_USER"], password=environ["ORACLE_PASS"],
                dsn=oracledb.makedsn(environ["ORACLE_HOST"], port,
                                    service_name=environ["ORACLE_NAME"]),
            ) as source:
                source.outputtypehandler = output_type_handler
                with source.cursor() as cursor:
                    await cursor.execute("ALTER SESSION SET NLS_NUMERIC_CHARACTERS = '.,'")
                    await cursor.execute("ALTER SESSION SET TIME_ZONE = '+00:00'")
                    await cursor.execute("SET TRANSACTION READ ONLY")
                    async with await connect() as target:
                        await target.execute("SET LOCAL TIME ZONE 'UTC'")
                        await target.execute("SET LOCAL lock_timeout = '10s'")
                        await target.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
                        for table in sorted(request.tables, key=str.lower):
                            await target.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                                                 (f"{SCHEMA}.{table.lower()}",))
                        for table in request.tables:
                            cursor.arraysize = 1000
                            # Discover exact source spelling, including quoted lowercase columns.
                            base_query = f'SELECT * FROM "{owner}"."{table.upper()}"'
                            await cursor.execute(base_query + " WHERE 1 = 0")
                            metadata = cursor.description
                            names = [item.name.lower() for item in metadata]
                            if any(not IDENTIFIER.fullmatch(name) for name in names) or len(set(names)) != len(names):
                                raise HTTPException(422, f"Неподдерживаемые или неоднозначные имена колонок в {table}")
                            source_names = {item.name.lower(): item.name for item in metadata}
                            missing = [key for key in request.filters if key.lower() not in source_names]
                            if missing:
                                raise HTTPException(422, f"В {table} нет колонок фильтра: {', '.join(missing)}")
                            where = " AND ".join(
                                f'"{source_names[key.lower()]}" = :v{i}'
                                for i, key in enumerate(request.filters)
                            )
                            query = base_query + (" WHERE " + where if where else "")
                            await cursor.execute(query, {f"v{i}": value for i, value in enumerate(request.filters.values())})
                            types = [column_type(item) for item in metadata]
                            relation = sql.Identifier(SCHEMA, table.lower())
                            existing = await (await target.execute(
                                "SELECT column_name, data_type FROM information_schema.columns "
                                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                                (SCHEMA, table.lower()),
                            )).fetchall()
                            if request.mode == "replace":
                                await target.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(relation))
                            elif existing and [(r["column_name"], r["data_type"]) for r in existing] != list(zip(names, types)):
                                raise HTTPException(409, f"Структура {table} изменилась; нужен mode=replace")
                            if request.mode == "replace" or not existing:
                                definitions = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(kind)) for name, kind in zip(names, types))
                                await target.execute(sql.SQL("CREATE TABLE {} ({})").format(relation, definitions))
                            else:
                                condition = sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(key.lower())) for key in request.filters)
                                deletion = sql.SQL("DELETE FROM {}").format(relation)
                                if request.filters:
                                    deletion += sql.SQL(" WHERE ") + condition
                                await target.execute(deletion, tuple(request.filters.values()))
                            count = 0
                            async with target.cursor() as writer:
                                async with writer.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(relation, sql.SQL(", ").join(map(sql.Identifier, names)))) as copy:
                                    while rows := await cursor.fetchmany(1000):
                                        for row in rows:
                                            values = []
                                            for value, kind in zip(row, types):
                                                if isinstance(value, oracledb.AsyncLOB):
                                                    value = await value.read()
                                                values.append(Jsonb(value) if kind == "jsonb" and value is not None else value)
                                            await copy.write_row(values)
                                        count += len(rows)
                            results.append({"table": f"{SCHEMA}.{table.lower()}", "rows": count,
                                            "columns": dict(zip(names, types))})
        return {"mode": request.mode, "tables": results}
    except TimeoutError:
        raise HTTPException(504, "Истекло время импорта; изменения отменены") from None
    except oracledb.Error:
        logger.exception("Oracle import failed")
        raise HTTPException(502, "Ошибка чтения Oracle; изменения отменены. Подробности в журнале API") from None
