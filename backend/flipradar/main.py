"""FastAPI application factory.

Run with ``flipradar serve`` or ``uvicorn flipradar.main:get_app --factory``.  Importing this module has
no side effects (no app, no database) so tests can build isolated apps with :func:`create_app`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.deps import require_token
from .api.routers import (
    alerts,
    analytics,
    analyze,
    auth,
    calculator,
    collect,
    fee_profiles,
    fx,
    health,
    imports,
    inventory,
    listings,
    notifications,
    opportunities,
    products,
    sales,
    searches,
    sources,
)
from .api.routers import settings as settings_router
from .config import Settings, get_settings
from .db import create_all, init_engine, session_scope
from .security import RateLimiter, RateLimitMiddleware, SecurityHeadersMiddleware
from .services import scheduler
from .services.bootstrap import bootstrap
from .services.errors import Conflict, Invalid, NotFound
from .services.timeutil import install_utc_listeners

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "templates" / "index.html"
STATIC_DIR = WEB_DIR / "static"
API_PREFIX = "/api/v1"
DOCS_URL = "/api/docs"
OPENAPI_URL = f"{API_PREFIX}/openapi.json"

API_ROUTERS = (
    auth.router,
    sources.router,
    searches.router,
    collect.router,
    listings.router,
    products.router,
    opportunities.router,
    analyze.router,
    calculator.router,
    fee_profiles.router,
    inventory.router,
    sales.router,
    analytics.router,
    alerts.router,
    notifications.router,
    imports.router,
    settings_router.router,
    fx.router,
)

PLACEHOLDER_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FLIP RADAR</title></head>
<body><h1>FLIP RADAR</h1>
<p>The API is running. The web dashboard files are not installed in this build.</p>
<p>API documentation: <a href="/api/docs">/api/docs</a> &middot; health: <a href="/health">/health</a></p>
</body></html>"""


def _install_error_handlers(app: FastAPI) -> None:
    async def not_found(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    async def conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    async def invalid(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    async def not_implemented(_: Request, exc: Exception) -> JSONResponse:
        log.warning("not implemented: %s", exc)
        return JSONResponse({"detail": "This feature depends on a component that is not implemented yet."},
                            status_code=503)

    app.add_exception_handler(NotFound, not_found)
    app.add_exception_handler(Conflict, conflict)
    app.add_exception_handler(Invalid, invalid)
    app.add_exception_handler(NotImplementedError, not_implemented)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    install_utc_listeners()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        create_all()
        with session_scope() as session:
            bootstrap(session, settings)
        task = scheduler.start(settings) if settings.scheduler_enabled else None
        app.state.scheduler_task = task
        if settings.require_auth is False:
            log.warning("API authentication is DISABLED (FLIPRADAR_REQUIRE_AUTH=false); only do this on localhost")
        try:
            yield
        finally:
            await scheduler.stop(task)

    app = FastAPI(
        title="FLIP RADAR",
        version=__version__,
        description="Self-hosted resale arbitrage intelligence for the Mexican market (MXN).",
        lifespan=lifespan,
        docs_url=DOCS_URL,
        redoc_url=None,
        openapi_url=OPENAPI_URL,
    )
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute, 60.0)
    _install_error_handlers(app)

    # Middleware: the last added is the outermost.  Security headers wrap everything (incl. 429s).
    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.add_middleware(RateLimitMiddleware, limiter=app.state.rate_limiter)
    app.add_middleware(SecurityHeadersMiddleware, docs_paths=(DOCS_URL,))

    app.include_router(health.router)
    for router in API_ROUTERS:
        app.include_router(router, prefix=API_PREFIX, dependencies=[Depends(require_token)])

    @app.get("/", include_in_schema=False, response_model=None)
    def index() -> FileResponse | HTMLResponse:
        if INDEX_HTML.is_file():
            return FileResponse(INDEX_HTML, media_type="text/html")
        return HTMLResponse(PLACEHOLDER_HTML)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def get_app() -> FastAPI:
    """Factory for ``uvicorn --factory`` (reads settings from the environment / .env)."""
    logging.basicConfig(level=get_settings().log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
