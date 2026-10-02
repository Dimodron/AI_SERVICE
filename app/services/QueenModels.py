from contextlib import asynccontextmanager
from uuid import uuid4
from services.context_diagnostics import log_event, request_metrics
from config import settings

import httpx
from fastapi import HTTPException
from schemas.qwen import GenerationSettings


class QwenResponseError(Exception):
    """Ollama returned an invalid or incomplete response."""


_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def qwen_lifespan():
    global _client
    async with httpx.AsyncClient(
        base_url=settings.QWEN_URL.rstrip("/"),
        timeout=httpx.Timeout(connect=settings.QWEN_CONNECT_TIMEOUT, read=settings.QWEN_READ_TIMEOUT,
                              write=settings.QWEN_WRITE_TIMEOUT, pool=settings.QWEN_POOL_TIMEOUT),
        limits=httpx.Limits(max_connections=settings.QWEN_MAX_CONNECTIONS,
                            max_keepalive_connections=settings.QWEN_KEEPALIVE_CONNECTIONS),
        trust_env=False,
    ) as client:
        _client = client
        try:
            yield
        finally:
            _client = None


async def list_models() -> list[str]:
    if _client is None:
        raise RuntimeError("Ollama client is not initialized: start the application lifespan")
    response = await _client.get("/api/tags", timeout=settings.QWEN_METADATA_TIMEOUT)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise QwenResponseError("Некорректный JSON от Ollama") from exc
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise QwenResponseError("Ollama не вернула список моделей")
    names = []
    for model in data["models"]:
        if (
            not isinstance(model, dict)
            or not isinstance(model.get("name"), str)
            or not model["name"].strip()
        ):
            raise QwenResponseError("Некорректный список моделей Ollama")
        names.append(model["name"])
    return names


async def resolve_model(model: str | None) -> str:
    model = model if model is not None else settings.DEFAULT_MODEL
    models = await list_models()
    if not models:
        raise HTTPException(503, "В Ollama нет доступных моделей")
    if model not in models:
        raise HTTPException(422, f"Модель {model} не установлена в Ollama")
    return model


class QwenStrategy:
    def __init__(self, model: str, settings: GenerationSettings | None = None):
        self.model = model
        self.settings = settings or GenerationSettings()

    async def generate(self, prompt: str) -> str:
        data = await self._request("generate", {"prompt": prompt})
        answer = data.get("response")
        if not isinstance(answer, str):
            raise QwenResponseError("Некорректный ответ Ollama")
        return answer

    async def chat(self, messages: list[dict]) -> str:
        message = await self.chat_message(messages)
        return message["content"]

    async def chat_message(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        if any(message.get("images") for message in messages):
            await self.ensure_vision()
        payload = {"messages": messages}
        if tools:
            payload["tools"] = tools
        data = await self._request("chat", payload)
        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise QwenResponseError("Некорректный ответ Ollama")
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list):
            raise QwenResponseError("Некорректные вызовы инструментов Ollama")
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                raise QwenResponseError("Некорректный вызов инструмента Ollama")
            function = call["function"]
            if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), dict):
                raise QwenResponseError("Некорректные аргументы инструмента Ollama")
        return {**message, "role": "assistant", "_done_reason": data.get("done_reason")}

    async def ensure_vision(self) -> None:
        if _client is None:
            raise RuntimeError("Ollama client is not initialized: start the application lifespan")
        response = await _client.post("/api/show", json={"model": self.model}, timeout=settings.QWEN_METADATA_TIMEOUT)
        if response.status_code == 404:
            raise HTTPException(422, f"Модель {self.model} не установлена в Ollama")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise QwenResponseError("Некорректный JSON от Ollama") from exc
        if not isinstance(data, dict) or not isinstance(data.get("capabilities"), list):
            raise QwenResponseError("Ollama не вернула возможности модели")
        if "vision" not in data["capabilities"]:
            raise HTTPException(422, f"Модель {self.model} не поддерживает изображения")

    async def _request(self, endpoint: str, payload: dict) -> dict:
        if _client is None:
            raise RuntimeError("Ollama client is not initialized: start the application lifespan")
        generation = self.settings.model_dump(
            include={"think", "options", "keep_alive"}, exclude_none=True,
        )
        generation["options"] = {"num_ctx": settings.QWEN_NUM_CTX, **generation.get("options", {})}
        body = {
            "model": self.model, **payload, "stream": False,
            "keep_alive": "30m", **generation,
            # Ollama otherwise silently retains a prefix and tail on overflow.
            # Never present a partial file as a fully analysed document.
            "truncate": False,
        }
        audit_id = str(uuid4()) if settings.CONTEXT_DIAGNOSTICS else None
        if audit_id:
            log_event("llm_request", audit_id=audit_id, endpoint=endpoint,
                      **request_metrics(body, settings.CONTEXT_DIAGNOSTIC_MARKERS))
        response = await _client.post(f"/api/{endpoint}", json=body)
        if audit_id:
            log_event("llm_http_response", audit_id=audit_id, status=response.status_code)
        if response.status_code == 404:
            raise HTTPException(422, f"Модель {self.model} не установлена в Ollama")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise QwenResponseError("Некорректный JSON от Ollama") from exc
        if not isinstance(data, dict) or data.get("done") is not True:
            raise QwenResponseError("Некорректный ответ Ollama")
        if audit_id:
            log_event("llm_result", audit_id=audit_id,
                      prompt_eval_count=data.get("prompt_eval_count"),
                      prompt_eval_cached_count=data.get("prompt_eval_cached_count"),
                      eval_count=data.get("eval_count"), done_reason=data.get("done_reason"))
        return data


async def ask_qwen(
    prompt: str, model: str | None = None, settings: GenerationSettings | None = None,
) -> str:
    return await QwenStrategy(await resolve_model(model), settings).generate(prompt)
