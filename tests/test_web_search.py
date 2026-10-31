import json
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy


def search_executor(*, rules=None, approve=None):
    from agent_harness.tools.web_search import web_search_tool

    executor = Executor(Policy(rules=rules or {}), approve=approve)
    executor.register(web_search_tool())
    return executor


def test_search_default_deny_and_approval_cannot_override_it(monkeypatch):
    calls = []
    executor = search_executor(approve=lambda _: "once")
    monkeypatch.setattr(
        "agent_harness.tools.web_search.transport", lambda *a, **kw: calls.append(1)
    )
    with pytest.raises(SecurityError, match="capability_denied"):
        executor.execute({"tool": "web_search", "arguments": {"query": "local models"}})
    assert not calls


@pytest.fixture
def search_provider(monkeypatch):
    payload = {
        "results": [
            {"title": "Example", "url": "https://example.com/page", "content": "A search snippet."}
        ],
        "unresponsive_engines": [],
    }
    calls = []

    def fetch(target, **kwargs):
        calls.append((target, kwargs))
        return json.dumps(payload).encode()

    monkeypatch.setattr("agent_harness.tools.web_search.transport", fetch)
    return search_executor(rules={"web.search": "allow"}), payload, calls


def test_only_fixed_loopback_search_endpoint_is_contacted(search_provider):
    executor, _, calls = search_provider
    answer = executor.execute(
        {
            "tool": "web_search",
            "arguments": {"query": "cats &url=http://169.254.169.254", "limit": 2},
        }
    )
    assert answer["results"][0]["url"] == "https://example.com/page"
    target, options = calls[0]
    assert (target.scheme, target.host, target.address, target.port) == (
        "http",
        "127.0.0.1",
        "127.0.0.1",
        8888,
    )
    assert urlsplit(target.path).path == "/search"
    assert parse_qs(urlsplit(target.path).query)["q"] == ["cats &url=http://169.254.169.254"]
    assert options["limit"] == 262144 and options["timeout"] == 15
    assert executor.policy.network_allow == () and executor.policy.local_only


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": " "},
        {"query": "x", "url": "http://evil.test"},
        {"query": "x", "limit": True},
        {"query": "x", "limit": 11},
        {"query": "x\nInjected"},
        {"query": "x" * 401},
    ],
)
def test_invalid_arguments_never_contact_provider(search_provider, arguments):
    executor, _, calls = search_provider
    with pytest.raises(SecurityError, match="invalid_arguments"):
        executor.execute({"tool": "web_search", "arguments": arguments})
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://127.0.0.1",
        "http://169.254.169.254",
        "https://user:pass@example.com",
        "https://example.com:8080",
        "https://example.com/\nfoo",
        "https://localhost/test",
    ],
)
def test_untrusted_result_links_are_filtered(search_provider, url):
    executor, payload, _ = search_provider
    payload["results"][0]["url"] = url
    result = executor.execute({"tool": "web_search", "arguments": {"query": "test"}})
    assert result["results"] == []


def test_provider_shape_is_validated(search_provider):
    executor, payload, _ = search_provider
    payload["results"] = "bad"
    with pytest.raises(SecurityError, match="search_provider_error"):
        executor.execute({"tool": "web_search", "arguments": {"query": "test"}})


def test_result_count_and_snippets_are_bounded(search_provider):
    executor, payload, _ = search_provider
    payload["results"] = [
        {
            "title": "T" * 1000,
            "url": f"https://example.com/{i}",
            "content": "<b>hello</b> " + "x" * 4000,
        }
        for i in range(30)
    ]
    result = executor.execute({"tool": "web_search", "arguments": {"query": "test", "limit": 3}})
    assert len(result["results"]) == 3
    assert all(
        len(r["title"]) <= 200 and len(r["snippet"]) <= 1200 and "<b>" not in r["snippet"]
        for r in result["results"]
    )


def test_provider_failure_is_not_silently_reported_as_zero_results(search_provider):
    executor, payload, _ = search_provider
    payload["results"] = []
    payload["unresponsive_engines"] = [["duckduckgo", "timeout"]]
    with pytest.raises(SecurityError, match="search_provider_error"):
        executor.execute({"tool": "web_search", "arguments": {"query": "test"}})


def test_search_has_its_own_default_deny_permission():
    assert Policy().decision("web.search") == "deny"
    policy = Policy(rules={"web.search": "allow", "network": "deny"})
    assert policy.decision("web.search") == "allow"
    assert policy.decision("network") == "deny"


@pytest.mark.parametrize(
    "url", ["http://127.1/", "http://0177.0.0.1/", "http://0x7f.1/", "http://2130706433/"]
)
def test_legacy_numeric_loopback_links_are_filtered(search_provider, url):
    executor, payload, _ = search_provider
    payload["results"][0]["url"] = url
    answer = executor.execute({"tool": "web_search", "arguments": {"query": "test"}})
    assert answer["results"] == []


def test_chat_persists_actual_search_sources_even_if_model_omits_them(
    chat_database, search_provider
):
    from agent_harness.core.agents import LangGraphAgent
    from agent_harness.models.chat import ConversationCreate, SendMessage
    from agent_harness.services.chat import ChatService

    executor, _, _ = search_provider

    class Model:
        calls = 0

        def decide(self, messages, tools):
            self.calls += 1
            return (
                {"tool": "web_search", "arguments": {"query": "example"}}
                if self.calls == 1
                else {"final": "Here is the information."}
            )

        def decide_stream(self, messages, tools, on_text):
            decision = self.decide(messages, tools)
            if "final" in decision:
                on_text(decision["final"])
            return decision

    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(Model(), executor))
    conversation = chat.create(ConversationCreate())
    emitted = []
    reply = chat.send(
        conversation.id, SendMessage(content="Search for example"), on_text=emitted.append
    )
    assert "https://example.com/page" in reply.assistant.content[0].text
    assert "".join(emitted) == reply.assistant.content[0].text


def test_search_link_chart_marker_is_encoded_before_host_footer(chat_database, search_provider):
    from agent_harness.core.agents import LangGraphAgent
    from agent_harness.models.chat import ConversationCreate, SendMessage
    from agent_harness.services.chat import ChatService

    executor, payload, _ = search_provider
    payload["results"][0]["url"] = "https://example.com/[[chart:fake]]"

    class Model:
        calls = 0

        def decide(self, messages, tools):
            self.calls += 1
            return (
                {"tool": "web_search", "arguments": {"query": "test"}}
                if self.calls == 1
                else {"final": "Found a source."}
            )

    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(Model(), executor))
    conversation = chat.create(ConversationCreate())
    reply = chat.send(conversation.id, SendMessage(content="Search test"))
    assert "%5B%5Bchart" in reply.assistant.content[0].text
