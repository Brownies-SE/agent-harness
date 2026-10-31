import pytest
from pydantic import ValidationError

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.models.chat import ConversationCreate, SendMessage
from agent_harness.services.chat import ChatService
from agent_harness.tools import builtin_tools

SPEC = {
    "kind": "bar",
    "title": "Example",
    "labels": ["A", "B"],
    "series": [{"name": "Count", "values": [1, 2]}],
}


def chart_executor(**kwargs):
    executor = Executor(Policy(), **kwargs)
    for tool in builtin_tools(executor.policy):
        executor.register(tool)
    return executor


def test_chart_tool_is_typed_local_and_cannot_execute_code_or_relax_deny():
    from agent_harness.core.errors import SecurityError

    executor = chart_executor()
    result = executor.execute({"tool": "chart_tool", "arguments": SPEC})
    assert result["kind"] == "bar"
    for arguments in [{**SPEC, "code": "print('unsafe')"}, {**SPEC, "approved": True}]:
        with pytest.raises(SecurityError, match="invalid_arguments"):
            executor.execute({"tool": "chart_tool", "arguments": arguments})
    denied = chart_executor(tool_rules={"chart_tool": "deny"})
    with pytest.raises(SecurityError, match="capability_denied"):
        denied.execute({"tool": "chart_tool", "arguments": SPEC})


@pytest.mark.parametrize(
    "spec",
    [
        {**SPEC, "series": [{"name": "bad", "values": [float("nan"), 1]}]},
        {**SPEC, "labels": ["one"]},
        {"kind": "mermaid", "mermaid": '%%{init: {"securityLevel":"loose"}}%%\ngraph TD; A-->B'},
        {"kind": "mermaid", "mermaid": 'flowchart TD\nA["<img src=x onerror=alert(1)>"]'},
        {"kind": "mermaid", "mermaid": 'flowchart TD\nA-->B\nclick A "https://example.com"'},
        {"kind": "mermaid", "mermaid": "---\nconfig: {}\n---\nflowchart TD\nA-->B"},
        {"kind": "mermaid", "mermaid": 'flowchart TD\nA@{ img: "https://example.com/a.png" }'},
        {"kind": "pie", "labels": ["A"], "series": [{"name": "n", "values": [-1]}]},
    ],
)
def test_chart_spec_rejects_malformed_or_active_content(spec):
    from agent_harness.models.charts import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec.model_validate(spec)


def test_diagrams_accept_standard_arrows():
    from agent_harness.models.charts import ChartSpec

    assert ChartSpec(kind="mermaid", mermaid="flowchart TD\nA[Start] --> B[Done]").kind == "mermaid"


def test_only_successful_executor_chart_results_are_saved_with_reply_variants(chat_database):
    from agent_harness.models.chat import ReplyRating

    class Model:
        def __init__(self):
            self.calls = 0

        def decide(self, messages, tools):
            self.calls += 1
            return (
                {"tool": "chart_tool", "arguments": SPEC}
                if self.calls == 1
                else {"final": "Here is the chart."}
            )

    database, owner = chat_database
    service = ChatService(database, owner, lambda: LangGraphAgent(Model(), chart_executor()))
    conversation = service.create(ConversationCreate())
    exchange = service.send(conversation.id, SendMessage(content="Plot the supplied example"))
    assert [block.type for block in exchange.assistant.content] == ["text", "chart"]
    assert exchange.assistant.content[1].spec.labels == ["A", "B"]
    assert exchange.assistant.variants[0].content == exchange.assistant.content
    saved = service.messages(conversation.id)[1]
    assert saved.content == exchange.assistant.content
    regenerated = service.regenerate(conversation.id, saved.id)
    assert len(regenerated.variants) == 2
    assert regenerated.variants[1].content[1].spec.kind == "bar"
    assert (
        service.select_variant(conversation.id, saved.id, regenerated.variants[0].id).content
        == saved.content
    )
    assert ReplyRating(variant_id=saved.variants[0].id, rating="up").rating == "up"


def test_denied_chart_is_not_attached_to_the_reply(chat_database):
    class Model:
        calls = 0

        def decide(self, messages, tools):
            self.calls += 1
            return (
                {"tool": "chart_tool", "arguments": SPEC}
                if self.calls == 1
                else {"final": "Denied."}
            )

    database, owner = chat_database
    service = ChatService(
        database,
        owner,
        lambda: LangGraphAgent(Model(), chart_executor(tool_rules={"chart_tool": "deny"})),
    )
    conversation = service.create(ConversationCreate())
    reply = service.send(conversation.id, SendMessage(content="test"))
    assert [block.type for block in reply.assistant.content] == ["text"]


@pytest.mark.parametrize(
    "source",
    [
        'flowchart TD\nA@{ "img": "/private" }',
        'flowchart TD\nA["![image](/private)"]',
    ],
)
def test_diagram_cannot_load_quoted_or_markdown_images(source):
    from agent_harness.models.charts import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(kind="mermaid", mermaid=source)


@pytest.mark.parametrize("label", ["bad\x00", "\ud800"])
def test_chart_text_rejects_invalid_unicode(label):
    from agent_harness.models.charts import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(kind="bar", labels=[label], series=[{"name": "n", "values": [1]}])


def test_mermaid_encoded_image_metadata_cannot_initiate_hidden_requests():
    from agent_harness.models.charts import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(kind="mermaid", mermaid='flowchart TD\nA@{ "\\u0069mg": "/api/local-action" }')


def test_stream_only_attaches_chart_after_committed_completion(chat_database):
    import json

    from fastapi.testclient import TestClient

    from agent_harness.api import create_app

    class Model:
        calls = 0

        def decide_stream(self, messages, tools, on_text):
            self.calls += 1
            if self.calls == 1:
                return {"tool": "chart_tool", "arguments": SPEC}
            on_text("Here is the chart.")
            return {"final": "Here is the chart."}

    database, owner = chat_database
    service = ChatService(database, owner, lambda: LangGraphAgent(Model(), chart_executor()))
    conversation = service.create(ConversationCreate())
    token = "synthetic-chart-stream-token-at-least-32-characters"  # noqa: S105
    app = create_app(service, token=token, model_name="test")
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + token}
    ) as client:
        response = client.post(
            f"/api/conversations/{conversation.id}/messages/stream",
            json={"content": "Chart please"},
        )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == ["delta", "complete"]
    blocks = events[-1]["exchange"]["assistant"]["content"]
    assert blocks[1]["type"] == "chart"
    assert service.messages(conversation.id)[1].content[1].spec.kind == "bar"
