import json

import pytest

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.models.chat import ConversationCreate, SendMessage
from agent_harness.services.chat import ChatService
from agent_harness.tools.charts import chart_tool


def executor(deny=False):
    result = Executor(Policy(), tool_rules={"chart_tool": "deny"} if deny else None)
    result.register(chart_tool())
    return result


class MissingChart:
    def __init__(self, kind, denied=False):
        self.kind = kind
        self.calls = 0
        self.denied = denied

    def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"final": f"Here is a {self.kind} chart displaying example data."}
        if self.calls == 2:
            return {
                "tool": "chart_tool",
                "arguments": {
                    "kind": self.kind,
                    "labels": ["1", "2", "3"],
                    "series": [{"name": "Example", "values": [10, 20, 30]}],
                },
            }
        if self.denied:
            return {"final": "I could not create the chart because permission was denied."}
        result = json.loads(messages[-1]["content"])["data"]
        return {"final": "Example data. " + result["reference"] + " Values increase."}

    def decide_stream(self, messages, tools, on_text):
        decision = self.decide(messages, tools)
        if "final" in decision:
            on_text(decision["final"])
        return decision


@pytest.mark.parametrize("kind", ["line", "scatter"])
@pytest.mark.parametrize("stream", [False, True])
def test_claimed_chart_requires_real_executor_result_before_reply_is_saved(
    chat_database, kind, stream
):
    database, owner = chat_database
    models = []

    def agent():
        model = MissingChart(kind)
        models.append(model)
        return LangGraphAgent(model, executor())

    chat = ChatService(database, owner, agent)
    conversation = chat.create(ConversationCreate())
    text = []
    reply = chat.send(
        conversation.id,
        SendMessage(content=f"Show a {kind} chart with example data"),
        on_text=text.append if stream else None,
    )
    assert [block.spec.kind for block in reply.assistant.content if block.type == "chart"] == [kind]
    assert models[0].calls == 3
    assert "displaying example data" not in "".join(text)
    assert len(chat.messages(conversation.id)) == 2


def test_correction_cannot_bypass_chart_deny(chat_database):
    database, owner = chat_database
    model = MissingChart("line", denied=True)
    chat = ChatService(database, owner, lambda: LangGraphAgent(model, executor(deny=True)))
    conversation = chat.create(ConversationCreate())
    emitted = []
    reply = chat.send(
        conversation.id, SendMessage(content="Show a line chart"), on_text=emitted.append
    )
    assert [block.type for block in reply.assistant.content] == ["text"]
    assert "permission was denied" in reply.assistant.content[0].text
    assert "displaying example data" not in "".join(emitted)


def test_repeated_phantom_chart_claims_are_bounded_and_not_saved(chat_database):
    class Liar:
        def decide(self, messages, tools):
            return {"final": "Here is a scatter chart displaying example data."}

    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(Liar(), executor()))
    conversation = chat.create(ConversationCreate())
    with pytest.raises(SecurityError):
        chat.send(conversation.id, SendMessage(content="Show a scatter chart"))
    assert chat.messages(conversation.id) == []


def test_followup_model_context_contains_prior_chart_data_not_only_prose(chat_database):
    models = []

    class Model:
        def __init__(self):
            self.calls = 0
            self.context = []

        def decide(self, messages, tools):
            self.context = messages
            self.calls += 1
            if len(models) == 1 and self.calls == 1:
                return {
                    "tool": "chart_tool",
                    "arguments": {
                        "kind": "bar",
                        "labels": ["Jan", "Feb"],
                        "series": [{"name": "Sales", "values": [10, 20]}],
                    },
                }
            return {"final": "Done."}

    def factory():
        model = Model()
        models.append(model)
        return LangGraphAgent(model, executor())

    database, owner = chat_database
    chat = ChatService(database, owner, factory)
    conversation = chat.create(ConversationCreate())
    chat.send(conversation.id, SendMessage(content="Show a bar chart"))
    chat.send(conversation.id, SendMessage(content="What were those values?"))
    previous = [
        json.loads(item["content"]) for item in models[1].context if item["role"] == "assistant"
    ]
    assert json.loads(previous[0]["final"].split("previous_visuals: ", 1)[1])[0]["series"][0][
        "values"
    ] == [10, 20]
    assert "chart_id" not in json.dumps(previous)


@pytest.mark.parametrize(
    "claim",
    [
        "Here is a **line chart** displaying example data.",
        "Here are the requested line and scatter charts.",
        "The line chart illustrates monthly sales.",
    ],
)
def test_common_chart_claims_cannot_skip_tool(chat_database, claim):
    class Model(MissingChart):
        def decide(self, messages, tools):
            if self.calls == 0:
                self.calls += 1
                return {"final": claim}
            return super().decide(messages, tools)

    database, owner = chat_database
    model = Model("line")
    chat = ChatService(database, owner, lambda: LangGraphAgent(model, executor()))
    conversation = chat.create(ConversationCreate())
    reply = chat.send(conversation.id, SendMessage(content="Show a line chart"))
    assert any(block.type == "chart" for block in reply.assistant.content)


def test_explaining_chart_concepts_does_not_require_creating_a_chart(chat_database):
    class Model:
        def decide(self, messages, tools):
            return {"final": "The line chart shows changes over time."}

    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(Model(), executor()))
    conversation = chat.create(ConversationCreate())
    reply = chat.send(conversation.id, SendMessage(content="Explain what a line chart shows."))
    assert [block.type for block in reply.assistant.content] == ["text"]
