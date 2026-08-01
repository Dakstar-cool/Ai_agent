from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager, nullcontext
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

import app.api.routes.runs as run_routes
from app import __version__
from app.api.routes.chat import close_orchestrator, require_api_key
from app.api.routes.chat import router as chat_router
from app.api.routes.coding import router as coding_router
from app.api.routes.memory import router as memory_router
from app.api.routes.providers import router as providers_router
from app.config.settings import get_settings
from app.contracts import PROTOCOL_VERSION
from app.errors import AppError
from app.utils.logging import configure_logging
from app.utils.observability import configure_opentelemetry
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
        json_logs=settings.log_json,
    )
    telemetry = (
        configure_opentelemetry(
            origin=str(settings.telemetry_exporter_otlp_endpoint),
            service_name=settings.telemetry_service_name,
            service_version=__version__,
        )
        if settings.telemetry_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        run_routes.get_run_service()
        try:
            yield
        finally:
            await run_routes.close_run_service()
            await close_orchestrator()
            if telemetry is not None:
                try:
                    telemetry.shutdown()
                except Exception as exc:  # noqa: BLE001  # pragma: no cover
                    logger.warning(
                        "telemetry_shutdown_failed error_type=%s",
                        exc.__class__.__name__,
                    )

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.rate_limit_state = {}
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(run_routes.router, prefix="/api/v1")
    app.include_router(coding_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    app.include_router(providers_router, prefix="/api/v1")

    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500
        span_context = (
            telemetry.start_request_span(request.method)
            if telemetry is not None
            else nullcontext(None)
        )
        logger.info(
            "request_started method=%s path=%s", request.method, request.url.path
        )
        with span_context as span:
            try:
                client_id = request.client.host if request.client else "unknown"
                allowed = _rate_limit_allows_request(
                    app.state.rate_limit_state,
                    client_id,
                    limit=settings.rate_limit_requests_per_minute,
                    now=time.monotonic(),
                )
                if not allowed:
                    status_code = 429
                    logger.warning(
                        "rate_limit_exceeded method=%s path=%s client=%s",
                        request.method,
                        request.url.path,
                        client_id,
                    )
                    response = JSONResponse(
                        status_code=status_code,
                        content={
                            "error": {
                                "code": "rate_limit_exceeded",
                                "message": "Too many requests",
                                "details": {
                                    "limit_per_minute": (
                                        settings.rate_limit_requests_per_minute
                                    )
                                },
                                "request_id": request_id,
                            },
                        },
                    )
                    response.headers["X-Request-ID"] = request_id
                    return response

                response = await call_next(request)
                status_code = response.status_code
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                if span is not None:
                    span.set_attribute("http.response.status_code", status_code)
                if telemetry is not None:
                    try:
                        telemetry.record_request(
                            method=request.method,
                            status_code=status_code,
                            duration_ms=duration_ms,
                        )
                    except Exception as exc:  # noqa: BLE001  # pragma: no cover
                        logger.warning(
                            "telemetry_record_failed error_type=%s",
                            exc.__class__.__name__,
                        )
                logger.info(
                    "request_finished method=%s path=%s duration_ms=%s",
                    request.method,
                    request.url.path,
                    duration_ms,
                )
                reset_request_id(token)

        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = get_request_id()
        logger.warning(
            "app_error path=%s code=%s message=%s",
            request.url.path,
            exc.code,
            exc.message,
        )
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
        logger.exception(
            "unhandled_exception path=%s error=%s",
            request.url.path,
            exc.__class__.__name__,
        )
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

    @app.get("/health", dependencies=[Depends(require_api_key)])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "env": settings.app_env,
            "component": "ai-agent-worker",
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
        }

    return app


app = create_app()
