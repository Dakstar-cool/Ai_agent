from __future__ import annotations

from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config.settings import Settings
from app.utils.observability import otlp_signal_endpoint


class CollectorHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, bytes]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.requests.append((self.path, self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, _format: str, *args: object) -> None:
        return None


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, name: str, value: object) -> None:
        self.attributes[name] = value


class FakeTelemetry:
    def __init__(self) -> None:
        self.span = FakeSpan()
        self.records: list[dict[str, object]] = []
        self.shutdown_called = False

    def start_request_span(self, method: str):
        self.span.attributes["http.request.method"] = method
        return nullcontext(self.span)

    def record_request(
        self, *, method: str, status_code: int, duration_ms: float
    ) -> None:
        self.records.append(
            {
                "method": method,
                "status_code": status_code,
                "duration_ms": duration_ms,
            }
        )

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_telemetry_is_opt_in_and_collector_origin_is_restricted() -> None:
    assert Settings(_env_file=None).telemetry_enabled is False
    assert otlp_signal_endpoint("https://collector.example.test/", "traces") == (
        "https://collector.example.test/v1/traces"
    )

    for endpoint in (
        None,
        "http://collector.example.test:4318",
        "https://user:secret@collector.example.test",
        "https://collector.example.test/custom/path",
    ):
        with pytest.raises(ValueError):
            Settings(
                _env_file=None,
                app_env="prod",
                telemetry_enabled=True,
                telemetry_exporter_otlp_endpoint=endpoint,
            )

    configured = Settings(
        _env_file=None,
        app_env="prod",
        telemetry_enabled=True,
        telemetry_exporter_otlp_endpoint="https://collector.example.test",
    )
    assert configured.telemetry_enabled is True


def test_enabled_telemetry_exports_only_bounded_http_metadata(
    monkeypatch, tmp_path
) -> None:
    telemetry = FakeTelemetry()
    settings = Settings(
        _env_file=None,
        app_env="test",
        telemetry_enabled=True,
        telemetry_exporter_otlp_endpoint="http://127.0.0.1:4318",
        log_to_file=False,
        state_db_path=str(tmp_path / "worker.sqlite3"),
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "configure_opentelemetry",
        lambda **_kwargs: telemetry,
    )

    with TestClient(main_module.create_app()) as client:
        response = client.get("/health?prompt=do-not-export")

    assert response.status_code == 200
    assert telemetry.shutdown_called is True
    assert telemetry.records
    exported = repr((telemetry.span.attributes, telemetry.records)).casefold()
    assert "do-not-export" not in exported
    assert "prompt" not in exported
    assert set(telemetry.span.attributes) == {
        "http.request.method",
        "http.response.status_code",
    }


def test_real_otlp_exporter_emits_traces_and_metrics_without_request_content(
    monkeypatch, tmp_path
) -> None:
    CollectorHandler.requests = []
    collector = ThreadingHTTPServer(("127.0.0.1", 0), CollectorHandler)
    thread = Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    marker = "prompt-secret-do-not-export"
    settings = Settings(
        _env_file=None,
        app_env="test",
        telemetry_enabled=True,
        telemetry_exporter_otlp_endpoint=(f"http://127.0.0.1:{collector.server_port}"),
        telemetry_service_name="ai-agent-worker-test",
        log_to_file=False,
        state_db_path=str(tmp_path / "worker.sqlite3"),
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    try:
        with TestClient(main_module.create_app()) as client:
            response = client.get(f"/health?prompt={marker}")
        assert response.status_code == 200
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=2)

    paths = {path for path, _body in CollectorHandler.requests}
    payload = b"".join(body for _path, body in CollectorHandler.requests)
    assert paths == {"/v1/traces", "/v1/metrics"}
    assert payload
    assert marker.encode() not in payload
