import pytest

from agent_harness.models.charts import ChartBlock, ChartSpec
from agent_harness.services.response_blocks import ResponseBlocks

SPEC = {"kind": "bar", "labels": ["A"], "series": [{"name": "n", "values": [1]}]}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("The chart above shows the totals.", "The chart shows the totals."),
        ("See the diagram below.", "See the diagram."),
        ("The above line chart compares sales.", "The line chart compares sales."),
        ("The graph shown above is useful.", "The graph is useful."),
        ("Values above 20 are highlighted.", "Values above 20 are highlighted."),
        ("Put the chart below the paragraph.", "Put the chart below the paragraph."),
        ("Use `chart above` as a literal label.", "Use `chart above` as a literal label."),
    ],
)
def test_visual_wording_is_neutral_without_changing_quantities_or_code(value, expected):
    from agent_harness.services.visual_wording import VisualWording

    chunks = []
    wording = VisualWording(chunks.append)
    for character in value:
        wording.feed(character)
    wording.finish()
    assert "".join(chunks) == expected


def test_split_streamed_visual_wording_matches_saved_blocks():
    chart_id = "a" * 32
    chart = ChartBlock(spec=ChartSpec.model_validate(SPEC))
    chunks = []
    response = ResponseBlocks({chart_id: chart}, on_text=chunks.append)
    for (
        character
    ) in f"The chart above shows totals.[[chart:{chart_id}]]The below graph is the same example.":
        response.feed(character)
    blocks = response.finish()
    assert blocks[0].text == "The chart shows totals."
    assert blocks[2].text == "The graph is the same example."
    assert "".join(chunks) == blocks[0].text + blocks[2].text


def test_text_only_answers_do_not_rewrite_spatial_language():
    response = ResponseBlocks({})
    response.feed("The chart above is from the article you quoted.")
    assert response.finish()[0].text == "The chart above is from the article you quoted."


def test_saved_legacy_reply_is_presented_consistently_without_rewriting_database(chat_database):
    from psycopg.types.json import Jsonb
    from test_chat_database import service

    from agent_harness.models.chat import ConversationCreate, SendMessage

    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    exchange = chat.send(conversation.id, SendMessage(content="example"))
    blocks = [
        {"type": "text", "text": "The chart above shows totals."},
        {"type": "chart", "spec": ChartSpec.model_validate(SPEC).model_dump()},
    ]
    with database.connect() as connection:
        connection.execute(
            "UPDATE agent_chat.messages SET content=%s WHERE id=%s",
            (Jsonb(blocks), exchange.assistant.id),
        )
        connection.execute(
            "UPDATE agent_chat.message_variants SET content=%s WHERE message_id=%s",
            (Jsonb(blocks), exchange.assistant.id),
        )
    saved = chat.messages(conversation.id)[1]
    assert saved.content[0].text == "The chart shows totals."
    assert saved.variants[0].content[0].text == "The chart shows totals."
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT content FROM agent_chat.messages WHERE id=%s", (exchange.assistant.id,)
            ).fetchone()["content"]
            == blocks
        )


def test_long_stream_keeps_words_and_code_intact_across_buffer_boundaries():
    from agent_harness.services.visual_wording import VisualWording

    value = (
        "Intro " * 20
        + "The chart above shows totals. "
        + "```text\n"
        + "chart above " * 30
        + "\n```\nThe above graph compares values."
    )
    expected = value.replace("The chart above shows", "The chart shows").replace(
        "The above graph", "The graph"
    )
    for size in [1, 7, 80, 4096]:
        chunks = []
        wording = VisualWording(chunks.append)
        for start in range(0, len(value), size):
            wording.feed(value[start : start + size])
        wording.finish()
        assert "".join(chunks) == expected


def test_long_answer_emits_before_generation_finishes():
    from agent_harness.services.visual_wording import VisualWording

    chunks = []
    wording = VisualWording(chunks.append)
    wording.feed("A sentence about the chart above. " * 10)
    assert chunks
    wording.finish()
    assert "".join(chunks) == "A sentence about the chart. " * 10


@pytest.mark.parametrize(
    "value",
    [
        "Put the chart below the paragraph. " + "x" * 65,
        "See the chart below 10.",
        "The graph above 20% is red.",
        "The chart below -20.",
        "The chart below it.",
    ],
)
def test_spatial_complements_and_quantities_are_preserved_for_every_chunk_size(value):
    from agent_harness.services.visual_wording import VisualWording

    for size in [1, 7, 80, 4096]:
        chunks = []
        wording = VisualWording(chunks.append)
        for start in range(0, len(value), size):
            wording.feed(value[start : start + size])
        wording.finish()
        assert "".join(chunks) == value
