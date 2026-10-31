import pytest

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy


def calculator(deny=False):
    from agent_harness.tools.calculator import calculator_tool

    executor = Executor(Policy(), tool_rules={"calculator_tool": "deny"} if deny else None)
    executor.register(calculator_tool())
    return executor


@pytest.mark.parametrize(
    "expression,result",
    [
        ("0.1 + 0.2", "0.3"),
        ("(120 * 0.15) + 8", "26"),
        ("2 ** 10", "1024"),
        ("-5 % 3", "-2"),
        ("7 // 2", "3"),
        ("1 / 4", "0.25"),
    ],
)
def test_arithmetic(expression, result):
    answer = calculator().execute(
        {"tool": "calculator_tool", "arguments": {"expression": expression}}
    )
    assert answer["result"] == result


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os")',
        "sum([1,2])",
        "(1).__class__",
        "[x for x in []]",
        "True + 1",
        "1/0",
        "1e9999",
        "2**999999999",
        "9" * 513,
        "(" * 100 + "1" + ")" * 100,
        "+".join(["1"] * 100),
        "2**(2**100)",
    ],
)
def test_unsafe_or_unbounded_expression_fails(expression):
    with pytest.raises(SecurityError):
        calculator().execute({"tool": "calculator_tool", "arguments": {"expression": expression}})


def test_tool_deny_still_applies():
    with pytest.raises(SecurityError, match="capability_denied"):
        calculator(True).execute({"tool": "calculator_tool", "arguments": {"expression": "2+2"}})


@pytest.mark.parametrize("expression", ["1e-9999", "1e-101", "1e-100 / 1e100"])
def test_nonzero_underflow_is_rejected_instead_of_reported_as_zero(expression):
    with pytest.raises(SecurityError, match="invalid_calculation"):
        calculator().execute({"tool": "calculator_tool", "arguments": {"expression": expression}})
