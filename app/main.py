from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import BASE_DIR, settings
from app.core.auth import LoginMiddleware, router as login_router
from app.routes import api, web, requests, suppliers, proformas, chat_search, catalog, matrices


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Cota.Preço.AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(LoginMiddleware)
app.include_router(login_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(web.router)
app.include_router(requests.router)
app.include_router(suppliers.router)
app.include_router(proformas.router)
app.include_router(chat_search.router)
app.include_router(catalog.router)
app.include_router(matrices.router)
app.include_router(api.router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
