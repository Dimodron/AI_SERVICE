import json
import logging

import httpx
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests
from services.QueenModels import QwenResponseError

logger = logging.getLogger(__name__)


async def service_error(request: Request, error: Exception):
    if isinstance(error, (psycopg.Error, PoolTimeout, PoolClosed, TooManyRequests)):
        status, detail = 503, "Хранилище PostgreSQL недоступно или занято"
    elif isinstance(error, httpx.PoolTimeout):
        status, detail = 503, "Сервис Qwen занят, повторите запрос позже"
    elif isinstance(error, httpx.TimeoutException):
        status, detail = 504, "Превышено время ожидания Qwen"
    elif isinstance(error, httpx.RequestError):
        status, detail = 503, "Сервер Qwen недоступен"
    elif isinstance(error, httpx.HTTPStatusError):
        upstream_status = error.response.status_code
        status = 422 if upstream_status in (400, 422) else 502
        detail = f"Ошибка Ollama (HTTP {upstream_status})"
        try:
            body = error.response.json()
        except ValueError:
            body = None
        reason = body.get("error") if isinstance(body, dict) else None
        if isinstance(reason, str):
            try:
                nested = json.loads(reason)
            except ValueError:
                nested = None
            if isinstance(nested, dict):
                reason = nested.get("error", nested)
        if isinstance(reason, dict):
            reason = reason.get("message")
        if isinstance(reason, str) and reason.strip():
            detail += ": " + " ".join(reason.split())[:1000]
        logger.warning(
            "Ollama %s %s: HTTP %s", error.request.method,
            error.request.url.path, upstream_status,
        )
    elif isinstance(error, QwenResponseError):
        status, detail = 502, str(error)
    else:
        status, detail = 502, "Ошибка ответа сервера Qwen"

    logger.warning(
        "%s %s: %s: %s", request.method, request.url.path,
        type(error).__name__, detail, exc_info=True,
    )
    content = {"detail": detail, "error_type": type(error).__name__}
    if isinstance(error, httpx.HTTPStatusError):
        content["upstream_status"] = error.response.status_code
    return JSONResponse(status_code=status, content=content)


def register_error_handlers(app: FastAPI):
    for error in (psycopg.Error, PoolTimeout, PoolClosed, TooManyRequests,
                  httpx.HTTPError, QwenResponseError):
        app.add_exception_handler(error, service_error)
