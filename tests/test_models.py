import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from agent_harness.executor import Executor, Tool
from agent_harness.models.chat import SendMessage
from agent_harness.models.tools import PathArguments, ToolRequest, WriteFileArguments
from agent_harness.policy import Policy


@pytest.mark.parametrize(
    "payload",
    [
        {"content": 123},
        {"content": ""},
        {"content": "   "},
        {"content": "hi", "policy": {"network": "allow"}},
        {"content": "hi", "role": "system"},
        {"content": "x" * 8001},
        {"content": "\ud800"},
    ],
)
def test_chat_input_rejects_coercion_and_authority(payload):
    with pytest.raises(ValidationError):
        SendMessage.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"tool": "read_file", "arguments": {"path": "x"}, "approved": True},
        {"tool": 1, "arguments": {}},
        {"tool": "read_file", "arguments": []},
    ],
)
def test_tool_envelope_is_strict(payload):
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(payload)


def test_builtin_arguments_are_closed_and_strict():
    assert PathArguments(path="note.txt").model_dump() == {"path": "note.txt"}
    for payload in ({"path": 1}, {"path": "x", "approved": True}):
        with pytest.raises(ValidationError):
            PathArguments.model_validate(payload)
    with pytest.raises(ValidationError):
        WriteFileArguments(path="x", content=False)


def test_host_argument_model_cannot_be_bypassed_by_permissive_schema():
    called = []
    executor = Executor(Policy())
    executor.register(
        Tool(
            "typed",
            {"type": "object", "additionalProperties": False, "properties": {"path": {}}},
            (),
            lambda **kw: called.append(kw),
            input_model=PathArguments,
        )
    )
    from agent_harness.errors import SecurityError

    with pytest.raises(SecurityError, match="invalid_arguments"):
        executor.execute({"tool": "typed", "arguments": {"path": 1}})
    assert called == []


def test_builtin_result_model_rejects_handler_type_confusion():
    class Result(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        written: int

    executor = Executor(Policy())
    executor.register(
        Tool(
            "typed_result",
            {"type": "object", "additionalProperties": False},
            (),
            lambda: {"written": True},
            output_model=Result,
        )
    )
    from agent_harness.errors import SecurityError

    with pytest.raises(SecurityError, match="tool_failed"):
        executor.execute({"tool": "typed_result", "arguments": {}})
