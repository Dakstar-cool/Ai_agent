from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass

from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Tracer


def otlp_signal_endpoint(origin: str, signal: str) -> str:
    if signal not in {"traces", "metrics"}:
        raise ValueError("Unsupported OTLP signal")
    return f"{origin.rstrip('/')}/v1/{signal}"


@dataclass(slots=True)
class TelemetryRuntime:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    tracer: Tracer
    request_counter: Counter
    request_duration: Histogram

    def start_request_span(self, method: str) -> AbstractContextManager[Span]:
        return self.tracer.start_as_current_span(
            "worker.http.request",
            attributes={"http.request.method": method},
        )

    def record_request(
        self, *, method: str, status_code: int, duration_ms: float
    ) -> None:
        attributes = {
            "http.request.method": method,
            "http.response.status_code": status_code,
        }
        self.request_counter.add(1, attributes)
        self.request_duration.record(duration_ms, attributes)

    def shutdown(self) -> None:
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()


def configure_opentelemetry(
    *, origin: str, service_name: str, service_version: str
) -> TelemetryRuntime:
    resource = Resource.create(
        {SERVICE_NAME: service_name, SERVICE_VERSION: service_version}
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=otlp_signal_endpoint(origin, "traces"),
                timeout=5,
            )
        )
    )
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=otlp_signal_endpoint(origin, "metrics"),
            timeout=5,
        )
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    tracer = tracer_provider.get_tracer("ai-agent-worker", service_version)
    meter = meter_provider.get_meter("ai-agent-worker", service_version)
    return TelemetryRuntime(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        tracer=tracer,
        request_counter=meter.create_counter("worker.http.server.request.count"),
        request_duration=meter.create_histogram(
            "worker.http.server.request.duration",
            unit="ms",
        ),
    )
