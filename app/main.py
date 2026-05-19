from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.chat import close_orchestrator
from app.api.routes.chat import router as chat_router
from app.config.settings import get_settings
from app.errors import AppError
from app.utils.logging import configure_logging
from app.utils.request_context import get_request_id, reset_request_id, set_request_id

logger = logging.getLogger(__name__)


def _rate_limit_allows_request(
    state: dict[str, tuple[float, int]],
    client_id: str,
    *,
    limit: int,
    now: float,
    window_seconds: float = 60.0,
) -> bool:
    if limit <= 0:
        return True

    window_started, count = state.get(client_id, (now, 0))
    if now - window_started >= window_seconds:
        state[client_id] = (now, 1)
        return True

    if count >= limit:
        return False

    state[client_id] = (window_started, count + 1)
    return True


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_dir=settings.log_dir,
        log_file_name=settings.log_file_name,
        log_to_file=settings.log_to_file,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await close_orchestrator()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.rate_limit_state = {}
    app.include_router(chat_router, prefix="/api/v1")

    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        token = set_request_id(request_id)
        started = time.perf_counter()
        logger.info("request_started method=%s path=%s", request.method, request.url.path)
        try:
            client_id = request.client.host if request.client else "unknown"
            allowed = _rate_limit_allows_request(
                app.state.rate_limit_state,
                client_id,
                limit=settings.rate_limit_requests_per_minute,
                now=time.monotonic(),
            )
            if not allowed:
                logger.warning("rate_limit_exceeded method=%s path=%s client=%s", request.method, request.url.path, client_id)
                response = JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "Too many requests",
                            "details": {"limit_per_minute": settings.rate_limit_requests_per_minute},
                            "request_id": request_id,
                        }
                    },
                )
                response.headers["X-Request-ID"] = request_id
                return response

            response = await call_next(request)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info("request_finished method=%s path=%s duration_ms=%s", request.method, request.url.path, duration_ms)
            reset_request_id(token)

        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = get_request_id()
        logger.warning("app_error path=%s code=%s message=%s", request.url.path, exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id()
        logger.exception("unhandled_exception path=%s error=%s", request.url.path, exc.__class__.__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "Internal server error",
                    "details": {},
                    "request_id": request_id,
                }
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
