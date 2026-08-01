from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.utils.request_context import get_request_id, get_run_id, get_task_id


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.run_id = get_run_id()
        record.task_id = get_task_id()
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "run_id": getattr(record, "run_id", "-"),
            "task_id": getattr(record, "task_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    level: str,
    log_dir: str = "logs",
    log_file_name: str = "app.log",
    log_to_file: bool = True,
    json_logs: bool = True,
) -> None:
    root_logger = logging.getLogger()
    if getattr(root_logger, "_local_ai_agent_configured", False):
        return

    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter: logging.Formatter
    if json_logs:
        formatter = JsonLogFormatter()
    else:
        formatter = logging.Formatter(
            fmt=(
                "%(asctime)s | %(levelname)s | %(name)s | "
                "request_id=%(request_id)s | run_id=%(run_id)s | "
                "task_id=%(task_id)s | %(message)s"
            )
        )
    request_filter = RequestIdFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(request_filter)
    root_logger.addHandler(console_handler)

    if log_to_file:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            Path(log_dir) / log_file_name,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        root_logger.addHandler(file_handler)

    root_logger._local_ai_agent_configured = True
