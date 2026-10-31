"""Interpret only bounded numeric AST nodes. Never compile or execute model code."""

import ast
import re
from decimal import Decimal, DecimalException, Underflow, localcontext

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Tool
from agent_harness.core.runtime import check_run
from agent_harness.models.calculator import CalculatorArguments, CalculatorResult

NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def calculate(expression):
    expression = expression.strip()
    try:
        if not expression or len(expression) > 512 or expression.count("(") > 32:
            raise ValueError
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 64:
            raise ValueError

        def number(node, depth=0):
            check_run()
            if depth > 16:
                raise ValueError
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                literal = ast.get_source_segment(expression, node)
                if literal is None or not NUMBER.fullmatch(literal):
                    raise ValueError
                result = Decimal(literal)
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand = number(node.operand, depth + 1)
                result = -operand if isinstance(node.op, ast.USub) else operand
            elif isinstance(node, ast.BinOp):
                left, right = number(node.left, depth + 1), number(node.right, depth + 1)
                if isinstance(node.op, ast.Add):
                    result = left + right
                elif isinstance(node.op, ast.Sub):
                    result = left - right
                elif isinstance(node.op, ast.Mult):
                    result = left * right
                elif isinstance(node.op, ast.Div):
                    result = left / right
                elif isinstance(node.op, ast.FloorDiv):
                    result = left // right
                elif isinstance(node.op, ast.Mod):
                    result = left % right
                elif isinstance(node.op, ast.Pow):
                    if right != right.to_integral_value() or abs(right) > 1000:
                        raise ValueError
                    result = left ** int(right)
                else:
                    raise ValueError
            else:
                raise ValueError
            if (
                not result.is_finite()
                or result.copy_abs() > Decimal("1e100")
                or (result and result.copy_abs() < Decimal("1e-100"))
            ):
                raise ValueError
            return result

        with localcontext() as context:
            context.prec = 28
            context.Emax = 100
            context.Emin = -100
            context.traps[Underflow] = True
            result = number(tree.body)
            return {"expression": expression, "result": str(result.normalize()), "precision": 28}
    except (ValueError, SyntaxError, RecursionError, DecimalException) as exc:
        raise SecurityError("invalid_calculation") from exc


def calculator_tool():
    return Tool(
        "calculator_tool",
        {
            **CalculatorArguments.model_json_schema(),
            "description": "Calculate numeric arithmetic using + - * / // % ** and parentheses. "
            "28-digit decimal precision; integer exponents only, magnitude at most 1000. "
            "No names, functions or code. // truncates toward zero; % follows the dividend sign. "
            "Return result as a decimal string to preserve precision.",
        },
        (),
        calculate,
        input_model=CalculatorArguments,
        output_model=CalculatorResult,
    )
