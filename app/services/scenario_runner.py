from config import settings

import json
import re
from uuid import uuid4

from fastapi import HTTPException
from psycopg import sql
from psycopg.errors import DataError, ProgrammingError, QueryCanceled
from schemas.qwen import ChatRequest
from schemas.reports import ReportCreate, ReportResponse, ReportToolRequest
from schemas.scenario_query import ScenarioQuery
from services.QueenModels import QwenStrategy
from services.reports import create_report

MAX_TOOL_CALLS = settings.MAX_TOOL_CALLS
MAX_RESULT_CHARS = settings.MAX_RESULT_CHARS
_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_INTERNAL_TABLES = {"users", "conversations", "messages", "files", "conversation_files", "message_files", "scenarios", "system_prompt"}
_OPERATORS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "like": "LIKE"}
QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_scenario",
        "description": "Чтение данных активного сценария с фильтром по user_jurpers. Поддерживает фильтры, SUM/COUNT/AVG/MIN/MAX и группировку. SQL формирует приложение.",
        "parameters": ScenarioQuery.model_json_schema(),
    },
}

REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "create_report",
        "description": "Создать файл XLSX, CSV или DOCX и получить ссылку. Для отчёта по данным БД передай query_result_id из query_scenario: сервер сам перенесёт строки. Для текстового документа используй text, для своей таблицы — columns и rows.",
        "parameters": ReportToolRequest.model_json_schema(),
    },
}


def _table_parts(name: str) -> tuple[str, str]:
    parts = name.split(".")
    if len(parts) == 1:
        parts.insert(0, "public")
    if len(parts) != 2 or not all(_IDENTIFIER.fullmatch(part) for part in parts):
        raise ValueError("Некорректное table_name в сценарии")
    schema, table = parts
    if schema.startswith("pg_") or schema == "information_schema" or table in _INTERNAL_TABLES:
        raise ValueError("Служебные таблицы недоступны для сценариев")
    return schema, table


async def query_scenario(connection, scenario: dict, query: ScenarioQuery, payload: ChatRequest, *, is_admin: bool = False) -> dict:
    # Ownership predicates are fixed by the application, never by the model.
    if not is_admin and scenario["visible_jurpers"] and payload.user_jurpers not in scenario["visible_jurpers"]:
        raise ValueError("Сценарий недоступен этому юрлицу")
    scope_column = "jurpers"
    if not scenario["table_name"]:
        raise ValueError("У сценария не указана таблица")
    schema, table = _table_parts(scenario["table_name"])
    allowed = set(scenario["columns_description"])
    if not allowed or not all(isinstance(name, str) and _IDENTIFIER.fullmatch(name) for name in allowed):
        raise ValueError("columns_description должен содержать имена доступных колонок как ключи")

    def column(name: str):
        if name not in allowed:
            raise ValueError(f"Колонка {name} не описана в сценарии")
        return sql.Identifier(name)

    cursor = await connection.execute(
        "SELECT a.attname FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND a.attnum > 0 AND NOT a.attisdropped", (schema, table),
    )
    print(cursor)
    actual = {row["attname"] for row in await cursor.fetchall()}
    if not allowed <= actual or (not is_admin and scope_column not in actual):
        raise ValueError("Таблица, описанные колонки или колонка принадлежности отсутствуют в БД")
    if not is_admin and payload.user_organization is not None and "organization" not in actual:
        raise ValueError("В таблице нет organization: нельзя выполнить запрошенную узкую выборку")
    expressions = []
    if query.aggregates:
        if query.columns:
            raise ValueError("Для агрегатов используй group_by вместо columns")
        expressions.extend(column(name) for name in query.group_by)
        for aggregate in query.aggregates:
            if aggregate.column == "*":
                if aggregate.function != "count":
                    raise ValueError("* разрешено только для count")
                argument, suffix = sql.SQL("*"), "all"
            else:
                argument, suffix = column(aggregate.column), aggregate.column
            expressions.append(sql.SQL("pg_catalog.{}({}) AS {}").format(
                sql.Identifier(aggregate.function), argument,
                sql.Identifier(f"{aggregate.function}_{suffix}"),
            ))
    else:
        if query.group_by:
            raise ValueError("group_by требует aggregates")
        expressions.extend(column(name) for name in (query.columns or sorted(allowed)))
    predicates = [sql.SQL("TRUE")] if is_admin else [sql.SQL("{} = %s").format(sql.Identifier(scope_column))]
    params = [] if is_admin else [payload.user_jurpers]
    if not is_admin and payload.user_organization is not None:
        predicates.append(sql.SQL('"organization" = %s'))
        params.append(payload.user_organization)
    for condition in query.filters:
        name = column(condition.column)
        if condition.operator in ("is_null", "is_not_null"):
            predicates.append(sql.SQL("{} IS {}NULL").format(
                name, sql.SQL("NOT " if condition.operator == "is_not_null" else ""),
            ))
        elif condition.value is None:
            raise ValueError("Для null используй is_null или is_not_null")
        else:
            predicates.append(sql.SQL("{} {} %s").format(name, sql.SQL(_OPERATORS[condition.operator])))
            params.append(condition.value)
    statement = sql.SQL("SELECT {} FROM {}.{} WHERE {}").format(
        sql.SQL(", ").join(expressions), sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(" AND ").join(predicates),
    )
    if query.group_by:
        statement += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(column(name) for name in query.group_by)
    statement += sql.SQL(" LIMIT %s")
    params.append(query.limit + 1)
    # A savepoint keeps a failed model query from breaking history writes.
    async with connection.transaction():
        cursor = await connection.execute("SELECT current_setting('statement_timeout') AS timeout")
        previous_timeout = (await cursor.fetchone())["timeout"]
        await connection.execute("SET LOCAL statement_timeout = '5s'")
        cursor = await connection.execute(statement, params)
        rows = await cursor.fetchall()
        await connection.execute("SELECT set_config('statement_timeout', %s, true)", (previous_timeout,))
    result = {"rows": rows[:query.limit], "truncated": len(rows) > query.limit}
    if len(json.dumps(result, ensure_ascii=False, default=str)) > MAX_RESULT_CHARS:
        raise ValueError("Результат слишком большой: уменьши limit, выбери меньше колонок или используй агрегаты")
    result["source"] = {"table": scenario["table_name"], "filters": [item.model_dump() for item in query.filters],
                        "jurpers": None if is_admin else payload.user_jurpers,
                        "organization": None if is_admin else payload.user_organization,
                        "all_jurpers": is_admin}
    return result


async def answer_with_scenarios(connection, model: str, payload: ChatRequest, messages: list[dict], scenarios: dict[str, dict], *, is_admin: bool = False) -> tuple[str, list[ReportResponse]]:
    strategy = QwenStrategy(model, payload)
    messages = list(messages)
    # Place tool instructions before the user context, after configured system prompts.
    index = next((i for i, message in enumerate(messages) if message["role"] != "system"), len(messages))
    messages.insert(index, {
        "role": "system",
        "content": (
            "Если пользователь просит отчёт или файл, вызови create_report в формате xlsx, csv "
            "или docx. Для данных из БД сначала вызови query_scenario, затем передай его "
            "query_result_id в create_report: не переписывай строки вручную. "
            "Не выдавай неполную выборку за полный отчёт. Для текстового документа передай text. "
            "Не выдумывай данные и ссылки; используй download_url успешного create_report. "
            "Если формат не указан, для таблицы выбери xlsx, для текстового документа — docx."
        ),
    })
    # Some model templates only retain one system turn. Preserve all instructions.
    if index:
        combined = "\n\n".join(item["content"] for item in messages[:index + 1])
        messages = [{"role": "system", "content": combined}, *messages[index + 1:]]
    query_tool = QUERY_TOOL
    if is_admin:
        query_tool = {**QUERY_TOOL, "function": {**QUERY_TOOL["function"],
                      "description": "Чтение данных активного сценария. Администратору доступны все юрлица; для конкретного юрлица или организации укажи filters. Поддерживает агрегаты и группировку."}}
    tools = [REPORT_TOOL, *([query_tool] if scenarios else [])]
    files: list[ReportResponse] = []
    query_results = {}
    calls_used = 0
    while True:
        reply = await strategy.chat_message(messages, tools)
        calls = reply.get("tool_calls", [])
        if not calls:
            answer = reply["content"]
            # Persist clickable links in message history even if the model omits them.
            for file in files:
                if file.download_url not in answer:
                    answer += f"\n\n[Скачать {file.filename}]({file.download_url})"
            return answer, files
        if calls_used + len(calls) > MAX_TOOL_CALLS:
            raise HTTPException(422, "Модель превысила лимит вызовов инструментов; уточните вопрос")
        messages.append(reply)
        for call in calls:
            function = call["function"]
            calls_used += 1
            try:
                if function["name"] == "query_scenario":
                    query = ScenarioQuery.model_validate(function["arguments"])
                    scenario = scenarios.get(str(query.scenario_id))
                    if scenario is None:
                        raise ValueError("Сценарий отсутствует или недоступен")
                    result = await query_scenario(connection, scenario, query, payload, is_admin=is_admin)
                    result_id = str(uuid4())
                    query_results[result_id] = result
                    result = {**result, "query_result_id": result_id}
                elif function["name"] == "create_report":
                    report_args = ReportToolRequest.model_validate(function["arguments"])
                    data = report_args.model_dump(exclude={"query_result_id"})
                    if report_args.query_result_id is not None:
                        source = query_results.get(report_args.query_result_id)
                        if source is None:
                            raise ValueError("Результат запроса отсутствует: сначала вызови query_scenario")
                        if source["truncated"]:
                            raise ValueError("Выборка неполная. Уточни фильтры или используй агрегаты перед созданием отчёта")
                        if report_args.rows or report_args.columns:
                            raise ValueError("С query_result_id не передавай rows и columns")
                        if not source["rows"]:
                            raise ValueError("Запрос не вернул строк для отчёта")
                        data["columns"] = list(source["rows"][0])
                        data["rows"] = [
                            [value if value is None or isinstance(value, (str, int, float, bool)) else str(value)
                             for value in row.values()] for row in source["rows"]
                        ]
                    report = ReportCreate.model_validate(data)
                    file = await create_report(connection, report)
                    files.append(file)
                    result = file.model_dump(mode="json")
                else:
                    raise ValueError("Неизвестный инструмент")
            except ValueError as error:
                result = {"error": str(error)[:2000]}
            except HTTPException as error:
                result = {"error": error.detail}
            except (DataError, ProgrammingError, QueryCanceled):
                result = {"error": "Не удалось прочитать данные: проверь колонки, типы фильтров и агрегаты; запрос ограничен 5 секундами"}
            messages.append({
                "role": "tool", "tool_name": function["name"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })
