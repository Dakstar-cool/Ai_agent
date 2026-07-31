from __future__ import annotations

from contextvars import ContextVar

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_run_id_var: ContextVar[str] = ContextVar("run_id", default="-")
_task_id_var: ContextVar[str] = ContextVar("task_id", default="-")


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str):
    return _request_id_var.set(request_id)


def reset_request_id(token) -> None:
    _request_id_var.reset(token)


def get_run_id() -> str:
    return _run_id_var.get()


def get_task_id() -> str:
    return _task_id_var.get()


def set_execution_context(run_id: str, task_id: str | None = None):
    return _run_id_var.set(run_id), _task_id_var.set(task_id or "-")


def reset_execution_context(tokens) -> None:
    run_token, task_token = tokens
    _run_id_var.reset(run_token)
    _task_id_var.reset(task_token)
