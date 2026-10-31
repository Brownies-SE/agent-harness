import json

import pytest

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.models.chat import ConversationCreate, SendMessage
from agent_harness.services.chat import ChatService
from agent_harness.tools.charts import chart_tool

SPEC = {"kind": "bar", "labels": ["A", "B"], "series": [{"name": "Count", "values": [1, 2]}]}


def executor():
    result = Executor(Policy())
    result.register(chart_tool())
    return result


def test_chart_references_are_host_generated_and_not_model_arguments():
    runner = executor()
    first = runner.execute({"tool": "chart_tool", "arguments": SPEC})
    second = runner.execute({"tool": "chart_tool", "arguments": SPEC})
    assert first["chart_id"] != second["chart_id"]
    assert len(first["chart_id"]) == 32
    with pytest.raises(SecurityError, match="invalid_arguments"):
        runner.execute({"tool": "chart_tool", "arguments": {**SPEC, "chart_id": first["chart_id"]}})


def test_split_references_emit_text_chart_text_without_leaking_markers():
    from agent_harness.models.charts import ChartBlock, ChartSpec
    from agent_harness.services.response_blocks import ResponseBlocks

    chart_id = "a" * 32
    chart = ChartBlock(spec=ChartSpec.model_validate(SPEC))
    events = []
    assembler = ResponseBlocks(
        {chart_id: chart},
        on_text=lambda text: events.append(("text", text)),
        on_chart=lambda block: events.append(("chart", block)),
    )
    for character in f"Before[[chart:{chart_id}]]After":
        assembler.feed(character)
    blocks = assembler.finish()
    assert [block.type for block in blocks] == ["text", "chart", "text"]
    assert blocks[0].text == "Before" and blocks[2].text == "After"
    before, _, after = events[:6], events[6], events[7:]
    assert "".join(text for kind, text in before) == "Before"
    assert events[6] == ("chart", chart)
    assert "".join(text for kind, text in after) == "After"


@pytest.mark.parametrize("value", ["0" * 32, "../other", "a" * 33])
def test_unknown_or_malformed_chart_references_fail_closed(value):
    from agent_harness.services.response_blocks import ResponseBlocks

    with pytest.raises(SecurityError, match="invalid_model_response"):
        assembler = ResponseBlocks({})
        assembler.feed(f"[[chart:{value}]]")
        assembler.finish()


def test_duplicate_or_unterminated_reference_fails_closed():
    from agent_harness.models.charts import ChartBlock, ChartSpec
    from agent_harness.services.response_blocks import ResponseBlocks

    chart_id = "a" * 32
    chart = ChartBlock(spec=ChartSpec.model_validate(SPEC))
    with pytest.raises(SecurityError, match="invalid_model_response"):
        assembler = ResponseBlocks({chart_id: chart})
        assembler.feed(f"[[chart:{chart_id}]][[chart:{chart_id}]]")
        assembler.finish()
    with pytest.raises(SecurityError, match="invalid_model_response"):
        assembler = ResponseBlocks({chart_id: chart})
        assembler.feed("Before[[chart:")
        assembler.finish()


class PositionedModel:
    def __init__(self, prefix="Before", suffix="After"):
        self.prefix = prefix
        self.suffix = suffix
        self.calls = 0

    def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"tool": "chart_tool", "arguments": SPEC}
        result = json.loads(messages[-1]["content"])["data"]
        return {"final": self.prefix + "[[chart:" + result["chart_id"] + "]]" + self.suffix}

    def decide_stream(self, messages, tools, on_text):
        decision = self.decide(messages, tools)
        if "final" in decision:
            for character in decision["final"]:
                on_text(character)
        return decision


@pytest.mark.parametrize("prefix,suffix", [("Before", "After"), ("", "After"), ("", "")])
def test_saved_and_regenerated_replies_preserve_chart_placement(chat_database, prefix, suffix):
    database, owner = chat_database
    chat = ChatService(
        database, owner, lambda: LangGraphAgent(PositionedModel(prefix, suffix), executor())
    )
    conversation = chat.create(ConversationCreate())
    exchange = chat.send(conversation.id, SendMessage(content="Example"))
    expected = (["text"] if prefix else []) + ["chart"] + (["text"] if suffix else [])
    assert [block.type for block in exchange.assistant.content] == expected
    saved = chat.messages(conversation.id)[1]
    regenerated = chat.regenerate(conversation.id, saved.id)
    assert [block.type for block in regenerated.variants[1].content] == expected
    assert (
        chat.select_variant(conversation.id, saved.id, saved.variants[0].id).content
        == saved.content
    )


def test_streamed_reply_preserves_order_and_saves_same_blocks(chat_database):
    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(PositionedModel(), executor()))
    conversation = chat.create(ConversationCreate())
    events = []
    exchange = chat.send(
        conversation.id,
        SendMessage(content="Example"),
        on_text=lambda text: events.append(("text", text)),
        on_chart=lambda chart: events.append(("chart", chart)),
    )
    assert [block.type for block in exchange.assistant.content] == ["text", "chart", "text"]
    assert "".join(value for kind, value in events if kind == "text") == "BeforeAfter"
    assert sum(kind == "chart" for kind, _ in events) == 1
    assert "[[chart:" not in exchange.assistant.model_dump_json()


def test_forged_chart_id_does_not_create_a_visual_or_save_a_turn(chat_database):
    class Forged:
        def decide(self, messages, tools):
            return {"final": "[[chart:" + "0" * 32 + "]]"}

    database, owner = chat_database
    chat = ChatService(database, owner, lambda: LangGraphAgent(Forged(), executor()))
    conversation = chat.create(ConversationCreate())
    with pytest.raises(SecurityError, match="invalid_model_response"):
        chat.send(conversation.id, SendMessage(content="test"))
    assert chat.messages(conversation.id) == []


def test_search_memory_and_next_turn_include_text_after_visuals(chat_database):
    from test_memory import Embeddings

    from agent_harness.models.chat import ConversationSearch
    from agent_harness.services.memory import MemoryService

    database, owner = chat_database
    chat = ChatService(
        database,
        owner,
        lambda: LangGraphAgent(PositionedModel("Before", "UniqueAfterChartText"), executor()),
    )
    conversation = chat.create(ConversationCreate())
    chat.send(conversation.id, SendMessage(content="Example"))
    hits = chat.search(ConversationSearch(query="UniqueAfterChartText"))
    assert [hit.conversation.id for hit in hits] == [conversation.id]
    assert "Before\n\nUniqueAfterChartText" in hits[0].snippet
    summaries = []

    def summarize(messages, purpose):
        summaries.append((messages, purpose))
        return "summary" if purpose == "compact" else "[]"

    memory = MemoryService(database, owner, Embeddings(), summarize)
    memory.compact(conversation.id)
    assert summaries[0][0][1]["content"] == "Before\n\nUniqueAfterChartText"
    memory.extract(conversation.id)
    assert summaries[1][0][1]["content"] == "Before\n\nUniqueAfterChartText"


def test_chart_first_history_does_not_break_context_or_memory(chat_database):
    from test_memory import Embeddings

    from agent_harness.services.memory import MemoryService

    database, owner = chat_database
    chat = ChatService(
        database, owner, lambda: LangGraphAgent(PositionedModel("", "After"), executor())
    )
    conversation = chat.create(ConversationCreate())
    chat.send(conversation.id, SendMessage(content="Example"))
    memory = MemoryService(database, owner, Embeddings(), lambda messages, purpose: "summary")
    memory.compact(conversation.id)
    # The next turn validates and reads chart-first messages without assuming text at index zero.
    chat.send(conversation.id, SendMessage(content="Another example"))
    assert len(chat.messages(conversation.id)) == 4
