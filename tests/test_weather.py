import pytest

from agent_harness.core.policy import Policy


def test_weather_capability_is_separate_and_defaults_to_deny():
    assert Policy().decision("weather.read") == "deny"
    policy = Policy(rules={"weather.read": "allow"})
    assert policy.decision("weather.read") == "allow"
    assert policy.decision("network") == "deny"
    assert policy.local_only


def test_user_allow_cannot_bypass_default_weather_deny(monkeypatch):
    from agent_harness.core.errors import SecurityError
    from agent_harness.core.executor import Executor
    from agent_harness.tools.weather import weather_tool

    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("DNS before approval"))
    executor = Executor(Policy(), tool_rules={"weather_tool": "allow"}, approve=lambda _: "once")
    executor.register(weather_tool(executor.policy))
    with pytest.raises(SecurityError, match="capability_denied"):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fc00::1",
        "224.0.0.1",
        "0.0.0.0",  # noqa: S104 — denied destination fixture
    ],
)
def test_weather_rejects_nonpublic_dns(monkeypatch, address):
    from agent_harness.core.errors import SecurityError
    from agent_harness.core.executor import Executor
    from agent_harness.tools.weather import weather_tool

    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    executor = Executor(Policy(rules={"weather.read": "allow"}))
    executor.register(weather_tool(executor.policy))
    with pytest.raises(SecurityError, match="network_address_denied"):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})


@pytest.fixture
def weather_provider(monkeypatch):
    import json

    from agent_harness.core.executor import Executor
    from agent_harness.tools.weather import weather_tool

    calls = []
    geo = {
        "results": [
            {
                "name": "Denver",
                "country": "United States",
                "latitude": 39.7,
                "longitude": -104.9,
                "timezone": "America/Denver",
            }
        ]
    }
    forecast = {
        "timezone": "America/Denver",
        "current": {
            "time": "2026-10-31T12:00",
            "temperature_2m": 55.0,
            "apparent_temperature": 53.0,
            "relative_humidity_2m": 40,
            "weather_code": 0,
            "wind_speed_10m": 5.0,
        },
        "daily": {
            "time": ["2026-10-31", "2026-11-01", "2026-11-02"],
            "weather_code": [0, 1, 2],
            "temperature_2m_max": [60, 61, 62],
            "temperature_2m_min": [30, 31, 32],
            "precipitation_probability_max": [0, 10, 20],
        },
        "current_units": {
            "temperature_2m": "°F",
            "apparent_temperature": "°F",
            "wind_speed_10m": "mp/h",
            "relative_humidity_2m": "%",
        },
        "daily_units": {
            "temperature_2m_max": "°F",
            "temperature_2m_min": "°F",
            "precipitation_probability_max": "%",
        },
    }
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])

    def fetch(target, **kwargs):
        calls.append((target, kwargs))
        return json.dumps(geo if len(calls) == 1 else forecast).encode()

    monkeypatch.setattr("agent_harness.tools.weather.transport", fetch)
    executor = Executor(Policy(rules={"weather.read": "allow"}))
    executor.register(weather_tool(executor.policy))
    return executor, geo, forecast, calls


def test_weather_success_keeps_generic_http_blocked(weather_provider):
    from urllib.parse import parse_qs, urlsplit

    from agent_harness.core.errors import SecurityError
    from agent_harness.tools.network import http_tool

    executor, _, _, calls = weather_provider
    result = executor.execute(
        {
            "tool": "weather_tool",
            "arguments": {"location": "Denver &x=http://127.0.0.1", "country_code": "us"},
        }
    )
    assert result["location"]["name"] == "Denver"
    assert result["current"]["temperature"] == 55
    assert len(result["daily"]) == 3
    assert "Open-Meteo" in result["source"]
    assert result["fetched_at"].endswith("+00:00")
    first, second = [item[0] for item in calls]
    assert (first.scheme, first.host, first.port, first.address) == (
        "https",
        "geocoding-api.open-meteo.com",
        443,
        "8.8.8.8",
    )
    assert parse_qs(urlsplit(first.path).query)["name"] == ["Denver &x=http://127.0.0.1"]
    assert parse_qs(urlsplit(first.path).query)["countryCode"] == ["US"]
    assert second.host == "api.open-meteo.com"
    assert second.path.startswith("/v1/forecast?")
    assert all(options == {"limit": 65536, "timeout": 10} for _, options in calls)
    assert executor.policy.local_only
    assert executor.policy.network_allow == ()
    executor.register(http_tool(executor.policy))
    with pytest.raises(SecurityError, match="network_destination_denied"):
        executor.execute(
            {"tool": "http_get", "arguments": {"url": "https://api.open-meteo.com/v1/forecast"}}
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"location": ""},
        {"location": " "},
        {"location": "a" * 101},
        {"location": "Denver", "url": "https://evil.test"},
        {"location": "Denver", "country_code": "USA"},
        {"location": "Denver", "country_code": "12"},
        {"location": "Denver", "forecast_days": True},
        {"location": "Denver", "forecast_days": 8},
        {"location": "Denver", "forecast_days": "3"},
        {"location": "Denver", "units": "kelvin"},
    ],
)
def test_weather_invalid_input_never_fetches(weather_provider, arguments):
    from agent_harness.core.errors import SecurityError

    executor, _, _, calls = weather_provider
    with pytest.raises(SecurityError, match="invalid_arguments"):
        executor.execute({"tool": "weather_tool", "arguments": arguments})
    assert calls == []


@pytest.mark.parametrize("bad", ["length", "nan", "string", "code", "units", "error", "date"])
def test_weather_untrusted_provider_validation(weather_provider, bad):
    from agent_harness.core.errors import SecurityError

    executor, _, forecast, _ = weather_provider
    if bad == "length":
        forecast["daily"]["temperature_2m_max"] = []
    elif bad == "nan":
        forecast["current"]["temperature_2m"] = float("nan")
    elif bad == "string":
        forecast["current"]["temperature_2m"] = "55"
    elif bad == "code":
        forecast["current"]["weather_code"] = 9000
    elif bad == "units":
        forecast["current_units"]["temperature_2m"] = "°C"
    elif bad == "error":
        forecast["error"] = True
    else:
        forecast["daily"]["time"][0] = "2026-13-33"
    with pytest.raises(SecurityError):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})


def test_weather_redirect_denied(weather_provider, monkeypatch):
    from agent_harness.core.errors import SecurityError

    def redirect(*args, **kwargs):
        raise SecurityError("http_status_denied")

    executor, _, _, _ = weather_provider
    monkeypatch.setattr("agent_harness.tools.weather.transport", redirect)
    with pytest.raises(SecurityError, match="http_status_denied"):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})


def test_weather_rejects_mixed_public_private_dns(weather_provider, monkeypatch):
    from agent_harness.core.errors import SecurityError

    executor, _, _, calls = weather_provider
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", (ip, 443)) for ip in ("8.8.8.8", "10.0.0.1")],
    )
    with pytest.raises(SecurityError, match="network_address_denied"):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})
    assert calls == []


def test_weather_approval_only_after_capability_and_before_dns(monkeypatch):
    from agent_harness.core.errors import SecurityError
    from agent_harness.core.executor import Executor
    from agent_harness.tools.weather import weather_tool

    approvals = []
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("DNS before approval"))
    executor = Executor(
        Policy(rules={"weather.read": "ask"}),
        approve=lambda request: approvals.append(request) or "deny",
    )
    executor.register(weather_tool(executor.policy))
    with pytest.raises(SecurityError, match="approval_denied"):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})
    assert len(approvals) == 1
    assert approvals[0]["capabilities"] == ("weather.read",)


def test_weather_schema_is_bounded_flat_and_closed():
    from agent_harness.core.schema import validate_schema
    from agent_harness.tools.weather import weather_tool

    tool = weather_tool(Policy())
    validate_schema(tool.schema)
    assert tool.schema["additionalProperties"] is False
    assert tool.schema["required"] == ["location"]
    assert tool.capabilities == ("weather.read",)
    assert not tool.resources


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": []},
        {
            "results": [
                {"name": "x", "country": "y", "latitude": 999, "longitude": 0, "timezone": "UTC"}
            ]
        },
    ],
)
def test_weather_geocoding_errors_fail_closed(weather_provider, payload):
    from agent_harness.core.errors import SecurityError

    executor, geo, _, calls = weather_provider
    geo.clear()
    geo.update(payload)
    with pytest.raises(SecurityError):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["apparent_temperature", "timezone"])
def test_weather_rejects_inconsistent_units_and_timezone(weather_provider, field):
    from agent_harness.core.errors import SecurityError

    executor, _, forecast, _ = weather_provider
    if field == "timezone":
        forecast["timezone"] = "Europe/London"
    else:
        forecast["current_units"]["apparent_temperature"] = "°C"
    with pytest.raises(SecurityError, match="weather_provider_error"):
        executor.execute({"tool": "weather_tool", "arguments": {"location": "Denver"}})


def test_chat_always_persists_weather_attribution_even_if_model_omits_it(
    chat_database, weather_provider
):
    from agent_harness.core.agents import LangGraphAgent
    from agent_harness.models.chat import ConversationCreate, SendMessage
    from agent_harness.services.chat import ChatService

    executor, _, _, _ = weather_provider

    class Model:
        calls = 0

        def decide(self, messages, tools):
            self.calls += 1
            return (
                {"tool": "weather_tool", "arguments": {"location": "Denver"}}
                if self.calls == 1
                else {"final": "The forecast is ready."}
            )

    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(Model(), executor))
    conversation = chat.create(ConversationCreate())
    exchange = chat.send(conversation.id, SendMessage(content="Weather please"))
    assert "[Open-Meteo](https://open-meteo.com/)" in exchange.assistant.content[0].text
    assert "CC BY 4.0" in chat.messages(conversation.id)[1].content[0].text


def test_weather_registry_does_not_break_cold_api_import():
    import subprocess
    import sys

    result = subprocess.run(  # noqa: S603 -- fixed interpreter and trusted literal module import
        [sys.executable, "-c", "from agent_harness.api import create_app"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
