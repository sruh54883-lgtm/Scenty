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
from database import DatabaseUnavailableError
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

    # FIX SCHEDULER-NO-WATCHDOG: wrap outer loop to catch BaseException
    try:
        while True:
            await asyncio.sleep(60)
            # FIX AUTH-RATELIMIT-MEMLEAK: periodic full sweep to evict expired IPs.
            # Per-request cleanup only visits one IP per request; rotating-IP DDoS
            # would leave O(N) empty lists without this sweep.
            try:
                now_sweep = time.time()
                stale_ips = [
                    ip for ip, ts_list in list(_auth_attempts.items())
                    if not [t for t in ts_list if now_sweep - t < _AUTH_WINDOW]
                ]
                for ip in stale_ips:
                    _auth_attempts.pop(ip, None)
                if stale_ips:
                    _log.debug("Auth rate-limit sweep: removed %d stale IPs", len(stale_ips))
            except Exception as _sweep_err:
                _log.warning("Auth rate-limit sweep error: %s", _sweep_err)
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
                    # FIX BROADCAST-DOUBLE-DELIVERY: mark is_sent=TRUE before the user
                    # loop so that a mid-loop restart does not re-blast all users.
                    await db.execute(
                        "UPDATE broadcasts SET is_sent = TRUE WHERE id = $1",
                        bc["id"],
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
                        "UPDATE broadcasts SET sent_count = $1 WHERE id = $2",
                        sent, bc["id"],
                    )
                    _log.info("Scheduled broadcast %s sent to %d users", bc["id"], sent)
            except Exception as _e:
                _log.error("Scheduled broadcast error: %s", _e)
    except BaseException as _be:
        _log.critical("Scheduler task exiting due to BaseException: %s", _be)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    ok = await db.connect()
    if not ok:
        import logging
        logging.getLogger("scenti.api").warning("Starting without DB — endpoints will return 503")
    _scheduler_task = None
    if ok:
        import logging as _logging
        _sched_log = _logging.getLogger("scenti.scheduler")
        _scheduler_task = asyncio.create_task(_scheduled_broadcast_worker())
        # FIX SCHEDULER-NO-WATCHDOG: log unexpected task exit
        def _on_scheduler_done(t: asyncio.Task) -> None:
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    _sched_log.error("Scheduler task exited unexpectedly: %s", exc)
                else:
                    _sched_log.error("Scheduler task exited unexpectedly with no exception")
        _scheduler_task.add_done_callback(_on_scheduler_done)
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
        # FIX CSP-001: add Content-Security-Policy header
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://telegram.org https://unpkg.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self';"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Не кешировать HTML страницы — webapp, admin, agent
        path = request.url.path
        if path in ("/webapp/", "/webapp", "/admin/", "/agent/") or (
            path.endswith(".html") and not path.startswith("/uploads")
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

app.add_middleware(_SecurityHeadersMiddleware)


# Rate limiting на /auth/* — не более 10 попыток в минуту с одного IP
_auth_attempts: dict[str, list[float]] = defaultdict(list)
_AUTH_LIMIT = 10
_AUTH_WINDOW = 60.0

class _AuthRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/auth/") and request.method == "POST":
            # FIX SEC-001: use request.client.host (set by trusted proxy) instead of
            # the leftmost X-Forwarded-For value which is fully attacker-controlled.
            ip = request.client.host if request.client else "unknown"
            now = time.time()
            hits = _auth_attempts[ip]
            fresh = [t for t in hits if now - t < _AUTH_WINDOW]
            # FIX AUTH-RATELIMIT-MEMLEAK: delete key when list is empty to prevent
            # unbounded dict growth under rotating-IP DDoS.
            if fresh:
                _auth_attempts[ip] = fresh
            elif ip in _auth_attempts:
                del _auth_attempts[ip]
            if len(_auth_attempts.get(ip, [])) >= _AUTH_LIMIT:
                return JSONResponse(status_code=429, content={"detail": "Слишком много попыток. Подождите минуту."})
            _auth_attempts.setdefault(ip, []).append(now)
        return await call_next(request)

app.add_middleware(_AuthRateLimitMiddleware)


class _AdminSPAMiddleware(BaseHTTPMiddleware):
    """Браузерные переходы на /admin/* без токена → редирект на /admin/ (SPA подхватит роутинг)."""
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method == "GET"
            and path.startswith("/admin/")
            and path not in ("/admin/", "/admin")
            and not path.startswith("/admin/index")
            and not path.startswith("/admin/static")
            and "text/html" in request.headers.get("accept", "")
            and not request.headers.get("authorization")
        ):
            return RedirectResponse("/admin/", status_code=302)
        return await call_next(request)


app.add_middleware(_AdminSPAMiddleware)


_ALLOWED_ORIGINS = [
    "https://scenti-production.up.railway.app",
    "https://web.telegram.org",
    # FIX SEC-002: removed "null" — accepts sandboxed-iframe / file:// attacks.
    # Telegram WebApp sends https://web.telegram.org; auth uses X-Telegram-Data header.
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Telegram-Data"],
)


@app.exception_handler(DatabaseUnavailableError)
async def db_unavailable_handler(request: Request, exc: DatabaseUnavailableError):
    """DB pool missing or broken → 503 so uptime monitors and Railway health checks
    can detect the outage and clients know to retry rather than treat it as a bug."""
    return JSONResponse(
        status_code=503,
        content={"detail": "База данных временно недоступна. Повторите запрос позже."},
        headers={"Retry-After": "30"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Не отдаём stack trace наружу."""
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


@app.get("/health")
async def health():
    """Return 200 only when the DB pool is alive; 503 otherwise so Railway and
    external uptime monitors can detect a broken instance and stop sending traffic."""
    try:
        db.get_pool()
    except DatabaseUnavailableError:
        return JSONResponse(
            status_code=503,
            content={"status": "db_unavailable"},
            headers={"Retry-After": "30"},
        )
    return {"status": "ok"}


@app.get("/")
async def root_redirect():
    resp = RedirectResponse(url="/webapp/", status_code=302)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.get("/admin")
async def admin_redirect():
    return RedirectResponse(url="/admin/", status_code=301)

@app.get("/agent")
async def agent_redirect():
    return RedirectResponse(url="/agent/", status_code=301)

@app.get("/webapp")
async def webapp_redirect():
    return RedirectResponse(url="/webapp/", status_code=301)


app.include_router(auth_router.router)
app.include_router(webapp.router)
app.include_router(admin.router)
app.include_router(agent.router)

# SPA-фолбэк: неизвестные пути внутри /admin/* → index.html (для клиентского роутинга)
_ROOT = Path(__file__).resolve().parent.parent
_admin_index = _ROOT / "admin" / "index.html"

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
            if response.status_code == 404:
                return await super().get_response("index.html", scope)
            return response
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise

class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles без кеширования HTML-файлов."""
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if getattr(response, "media_type", "") == "text/html":
            for k, v in _NO_CACHE.items():
                response.headers[k] = v
        return response

# Раздаём статику webapp, admin, agent прямо из FastAPI
for _name, _path, _cls in [
    ("webapp", _ROOT / "webapp", _NoCacheStaticFiles),
    ("admin",  _ROOT / "admin",  _NoCacheStaticFiles),
    ("agent",  _ROOT / "agent",  _NoCacheStaticFiles),
]:
    if _path.exists():
        app.mount(f"/{_name}", _cls(directory=str(_path), html=True), name=_name)

# Загруженные картинки
import os as _os
_uploads_dir = _os.environ.get("UPLOADS_DIR", "/app/uploads")
_os.makedirs(_uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")
