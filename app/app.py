from contextlib import asynccontextmanager

from database.history import database_lifespan
from fastapi import FastAPI
from routers.file_router import router as file_router
from routers.router import router
from services.errors import register_error_handlers
from services.QueenModels import qwen_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database_lifespan(), qwen_lifespan():
        yield


app = FastAPI(title="Qween API", lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)
app.include_router(file_router)


@app.get("/health", tags=["Service"])
async def health():
    return {"status": "ok"}
