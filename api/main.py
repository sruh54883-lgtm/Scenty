"""Scenti Loyalty API — FastAPI приложение."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import database as db
from routers import admin, agent, auth_router, webapp


@asynccontextmanager
async def lifespan(app: FastAPI):
    ok = await db.connect()
    if not ok:
        import logging
        logging.getLogger("scenti.api").warning("Starting without DB — endpoints will return 503")
    yield
    await db.disconnect()


app = FastAPI(title="Scenti Loyalty API", version="1.0.0", lifespan=lifespan)

# CORS — для dev разрешаем всё, позже сузим
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Не отдаём stack trace наружу."""
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(auth_router.router)
app.include_router(webapp.router)
app.include_router(admin.router)
app.include_router(agent.router)

# Раздаём статику webapp, admin, agent прямо из FastAPI
_ROOT = Path(__file__).resolve().parent.parent
for _name, _path in [("webapp", _ROOT / "webapp"), ("admin", _ROOT / "admin"), ("agent", _ROOT / "agent")]:
    if _path.exists():
        app.mount(f"/{_name}", StaticFiles(directory=str(_path), html=True), name=_name)
