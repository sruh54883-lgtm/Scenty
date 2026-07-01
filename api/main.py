"""Scenti Loyalty API — FastAPI приложение."""
import asyncio
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

import database as db
from routers import admin, agent, auth_router, webapp


async def _scheduled_broadcast_worker():
    """Каждую минуту проверяет и отправляет отложенные рассылки (is_sent=FALSE)."""
    import logging
    import sys
    from pathlib import Path as _Path
    _log = logging.getLogger("scenti.scheduler")

    _bot_path = str(_Path(__file__).resolve().parent.parent / "bot")
    if _bot_path not in sys.path:
        sys.path.insert(0, _bot_path)

    while True:
        await asyncio.sleep(60)
        try:
            pending = await db.fetch(
                "SELECT * FROM broadcasts WHERE is_sent = FALSE AND scheduled_at <= NOW()"
            )
            if not pending:
                continue
            from notifications import get_bot
            bot = get_bot()
            for bc in pending:
                # Параметризованный WHERE с корректной нумерацией ($1, $2, ...)
                conditions = ["is_active = TRUE"]
                params: list = []
                if bc.get("region_id"):
                    params.append(bc["region_id"])
                    conditions.append(f"region_id = ${len(params)}")
                if bc.get("lang_filter"):
                    params.append(bc["lang_filter"])
                    conditions.append(f"language = ${len(params)}")
                users = await db.fetch(
                    "SELECT telegram_id, language FROM users WHERE " + " AND ".join(conditions),
                    *params,
                )
                sent = 0
                for u in users:
                    tg_id = u["telegram_id"]
                    if not tg_id:
                        continue
                    lang = u["language"] or "ru"
                    text = (bc["message_uz"] or bc["message_ru"]) if lang == "uz" else bc["message_ru"]
                    if not text:
                        continue
                    try:
                        await bot.send_message(int(tg_id), text, parse_mode="HTML")
                        sent += 1
                        await asyncio.sleep(0.04)
                    except Exception:
                        pass
                await db.execute(
                    "UPDATE broadcasts SET is_sent = TRUE, sent_count = $1 WHERE id = $2",
                    sent, bc["id"],
                )
                _log.info("Scheduled broadcast %s sent to %d users", bc["id"], sent)
        except Exception as _e:
            _log.error("Scheduled broadcast error: %s", _e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ok = await db.connect()
    if not ok:
        import logging
        logging.getLogger("scenti.api").warning("Starting without DB — endpoints will return 503")
    _scheduler_task = None
    if ok:
        _scheduler_task = asyncio.create_task(_scheduled_broadcast_worker())
    yield
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except (asyncio.CancelledError, Exception):
            pass
    await db.disconnect()


app = FastAPI(title="Scenti Loyalty API", version="1.0.0", lifespan=lifespan)

# SPA browser fallback middleware — перехватывает браузерные GET запросы к путям,
# которые конфликтуют с API-роутами. Браузерный запрос = нет Authorization и Accept: text/html.
_ADMIN_SPA_CONFLICT_PATHS = {
    "/admin/users", "/admin/transactions", "/admin/gifts", "/admin/claims",
    "/admin/catalog", "/admin/regions", "/admin/agents", "/admin/stats",
    "/admin/backups", "/admin/privacy",
}

class _AdminBrowserFallback(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or request.url.path
        if (
            path in _ADMIN_SPA_CONFLICT_PATHS
            and request.method == "GET"
            and "text/html" in request.headers.get("accept", "")
            and "authorization" not in request.headers
        ):
            _idx = Path(__file__).resolve().parent.parent / "admin" / "index.html"
            return FileResponse(str(_idx))
        return await call_next(request)

app.add_middleware(_AdminBrowserFallback)


# Security headers на все ответы
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(_SecurityHeadersMiddleware)


# Rate limiting на /auth/* — не более 10 попыток в минуту с одного IP
_auth_attempts: dict[str, list[float]] = defaultdict(list)
_AUTH_LIMIT = 10
_AUTH_WINDOW = 60.0

class _AuthRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/auth/") and request.method == "POST":
            ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
            now = time.time()
            hits = _auth_attempts[ip]
            _auth_attempts[ip] = [t for t in hits if now - t < _AUTH_WINDOW]
            if len(_auth_attempts[ip]) >= _AUTH_LIMIT:
                return JSONResponse(status_code=429, content={"detail": "Слишком много попыток. Подождите минуту."})
            _auth_attempts[ip].append(now)
        return await call_next(request)

app.add_middleware(_AuthRateLimitMiddleware)


_ALLOWED_ORIGINS = [
    "https://scenti-production.up.railway.app",
    "https://web.telegram.org",
    "null",  # Telegram WebApp открывает через file:// / null origin
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Telegram-Data"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Не отдаём stack trace наружу."""
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/webapp", status_code=302)


app.include_router(auth_router.router)
app.include_router(webapp.router)
app.include_router(admin.router)
app.include_router(agent.router)

# SPA-фолбэк: неизвестные пути внутри /admin/* → index.html (для клиентского роутинга)
_ROOT = Path(__file__).resolve().parent.parent
_admin_index = _ROOT / "admin" / "index.html"

class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
            # html=True mode returns 404 Response instead of raising — catch both
            if response.status_code == 404:
                return await super().get_response("index.html", scope)
            return response
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise

# Раздаём статику webapp, admin, agent прямо из FastAPI
for _name, _path, _cls in [
    ("webapp", _ROOT / "webapp", StaticFiles),
    ("admin",  _ROOT / "admin",  _SPAStaticFiles),
    ("agent",  _ROOT / "agent",  StaticFiles),
]:
    if _path.exists():
        app.mount(f"/{_name}", _cls(directory=str(_path), html=True), name=_name)

# Загруженные картинки
import os as _os
_uploads_dir = _os.environ.get("UPLOADS_DIR", "/app/uploads")
_os.makedirs(_uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")
