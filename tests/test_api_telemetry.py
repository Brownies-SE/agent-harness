from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
from opentelemetry import trace

from agent_harness.api import create_app


def test_api_does_not_emit_spans_to_environment_configured_provider(monkeypatch):
    spans = []

    class Tracer(trace.NoOpTracer):
        @contextmanager
        def start_as_current_span(self, name, *args, **kwargs):
            spans.append(name)
            yield trace.INVALID_SPAN

    class Provider(trace.TracerProvider):
        def get_tracer(self, *args, **kwargs):
            return Tracer()

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: Provider())
    service = SimpleNamespace(database=SimpleNamespace(health=lambda: None))
    app = create_app(
        service,
        token="synthetic-local-telemetry-token-32-characters",  # noqa: S106 - test credential
        model_name="test",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(
            "/api/health",
            headers={"Authorization": "Bearer synthetic-local-telemetry-token-32-characters"},
        )
        assert response.status_code == 200
    assert spans == []
