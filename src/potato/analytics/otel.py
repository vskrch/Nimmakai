"""Optional OpenTelemetry OTLP export (requires potato[otel] extras)."""

from __future__ import annotations

import logging
from typing import Any

from potato.analytics.models import TraceRecord

logger = logging.getLogger(__name__)


class OTLPExporter:
    """
    Best-effort OTLP exporter using gen_ai.* semantic conventions.

    Install: ``pip install potato[otel]`` (opentelemetry-api + sdk + exporter).
    If packages are missing, ``enabled`` is False and calls are no-ops.
    """

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = (endpoint or "").strip() or None
        self.enabled = False
        self._tracer: Any = None
        if not self.endpoint:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": "potato"})
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint))
            )
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("potato.analytics")
            self.enabled = True
            logger.info("OTLP exporter enabled endpoint=%s", self.endpoint)
        except ImportError:
            logger.warning(
                "OTLP requested but opentelemetry packages missing — install potato[otel]"
            )
        except Exception:
            logger.exception("OTLP exporter init failed")

    def on_flush(self, batch: list[TraceRecord]) -> None:
        if not self.enabled or self._tracer is None:
            return
        from opentelemetry import trace

        for t in batch:
            with self._tracer.start_as_current_span(
                "gen_ai.chat",
                attributes={
                    "gen_ai.system": "potato",
                    "gen_ai.request.model": t.model_requested or "",
                    "gen_ai.response.model": t.model_routed or "",
                    "gen_ai.usage.input_tokens": t.prompt_tokens,
                    "gen_ai.usage.output_tokens": t.completion_tokens,
                    "potato.intent": t.intent or "",
                    "potato.trace_id": t.trace_id,
                    "potato.provider": t.provider_id or "",
                    "potato.fallback_index": t.fallback_index,
                },
            ) as span:
                if not t.success:
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    if t.error_message:
                        span.set_attribute("error.message", t.error_message)
