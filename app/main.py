from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.chat import router as chat_router
from app.config.settings import get_settings
from app.errors import AppError
from app.utils.logging import configure_logging
from app.utils.request_context import get_request_id, reset_request_id, set_request_id

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_dir=settings.log_dir,
        log_file_name=settings.log_file_name,
        log_to_file=settings.log_to_file,
    )

    app = FastAPI(title=settings.app_name)
    app.include_router(chat_router, prefix="/api/v1")

    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        token = set_request_id(request_id)
        started = time.perf_counter()
        logger.info("request_started method=%s path=%s", request.method, request.url.path)
        try:
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
