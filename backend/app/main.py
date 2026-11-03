"""FastAPI entrypoint. Run with: uv run uvicorn app.main:app --reload"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.auth import router as auth_router
from app.api.v1.collections import router as collections_router
from app.api.v1.prompts import router as prompts_router
from app.api.v1.routes import health_router, router
from app.api.v1.tools import router as tools_router
from app.auth.tokens import TokenVerifier
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.db.session import Database
from app.llm.registry import ProviderRegistry
from app.mcp.manager import MCPManager
from app.rag.embeddings import build_embedder
from app.rag.runtime import RagRuntime

logger = logging.getLogger("app.request")


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    database: Database | None = None,
    auth: TokenVerifier | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = registry or ProviderRegistry.from_settings(settings)
        app.state.database = database or (Database(settings.database_url) if settings.database_url else None)
        logger.info("prompt store: %s", "enabled" if app.state.database else "disabled (no DATABASE_URL)")
        embedder = build_embedder(settings) if app.state.database is not None else None
        app.state.rag = RagRuntime(app.state.database, embedder, settings)
        await app.state.rag.start()
        app.state.mcp = MCPManager(settings)  # connects to MCP servers on first use
        app.state.auth = auth or (TokenVerifier(settings) if settings.auth_enabled else None)
        if settings.auth_enabled:
            logger.info(
                "auth: enabled (issuer %s, audience %s)", settings.oidc_issuer, settings.oidc_audience
            )
        else:
            logger.warning(
                "auth: DISABLED (AUTH_ENABLED=false): every request acts as the dev user with roles %s. "
                "Local development only.",
                ",".join(settings.dev_user_roles),
            )
        yield
        if app.state.auth is not None and auth is None:
            await app.state.auth.aclose()
        await app.state.mcp.aclose()  # also stops stdio server processes
        await app.state.rag.stop()
        await app.state.registry.aclose()
        if app.state.database is not None and database is None:  # close only what we opened
            await app.state.database.aclose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "user": getattr(request.state, "user", None),  # set by get_principal (Layer 5)
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return response

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(prompts_router)
    app.include_router(collections_router)
    app.include_router(tools_router)
    return app


app = create_app()
