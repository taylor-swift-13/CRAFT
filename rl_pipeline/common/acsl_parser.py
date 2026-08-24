"""Fast, conservative parser for the model-facing scalar ACSL subset.

The parser is a prefilter, not a replacement for Frama-C.  It rejects only
expressions that cannot belong to the allowlisted grammar; every survivor is
still checked by the existing Frama-C kernel pass.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Iterable

from .program import Program, integer_source_constants
from .state import _acsl_to_py, _python_name, normalize_invariant


_AT_CALL = re.compile(
    r"\\at\(\s*([A-Za-z_]\w*)\s*,\s*(Pre|LoopEntry)\s*\)"
)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_FORBIDDEN_COMMAND = re.compile(r"\\(?!at\b)[A-Za-z_]+")
_TYPE_CAST = re.compile(
    r"\(\s*(?:(?:unsigned|signed)\s+)?"
    r"(?:char|short|int|long(?:\s+long)?|integer|real)\s*\)"
)
_FUNCTION_CALL = re.compile(r"(?<!\\)\b[A-Za-z_]\w*\s*\(")
_DIRECT_UNKNOWN_CALL = re.compile(r"unknown\w*\s*\(\s*\)\s*")


@dataclass(frozen=True)
class ParseVerdict:
    valid: bool
    reason: str = ""


class _InvalidExpression(ValueError):
    pass


def _kind(node: ast.AST, allowed_names: set[str]) -> str:
    """Return ``int`` or ``bool`` while enforcing the scalar grammar."""
    if isinstance(node, ast.Expression):
        return _kind(node.body, allowed_names)
    if isinstance(node, ast.Name):
        if node.id not in allowed_names:
            raise _InvalidExpression("out_of_scope")
        return "int"
    if isinstance(node, ast.Constant):
        if type(node.value) is bool:
            return "bool"
        if type(node.value) is int:
            return "int"
        raise _InvalidExpression("non_integer_literal")
    if isinstance(node, ast.UnaryOp):
        operand = _kind(node.operand, allowed_names)
        if isinstance(node.op, ast.Not):
            if operand != "bool":
                raise _InvalidExpression("not_on_integer")
            return "bool"
        if isinstance(node.op, (ast.UAdd, ast.USub)):
            if operand != "int":
                raise _InvalidExpression("arithmetic_on_boolean")
            return "int"
        raise _InvalidExpression("unsupported_unary_operator")
    if isinstance(node, ast.BinOp):
        if not isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
             ast.LShift, ast.RShift),
        ):
            raise _InvalidExpression("unsupported_arithmetic_operator")
        if _kind(node.left, allowed_names) != "int" or _kind(
            node.right, allowed_names
        ) != "int":
            raise _InvalidExpression("arithmetic_on_boolean")
        return "int"
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise _InvalidExpression("unsupported_logical_operator")
        if any(_kind(value, allowed_names) != "bool" for value in node.values):
            raise _InvalidExpression("logical_operator_on_integer")
        return "bool"
    if isinstance(node, ast.Compare):
        left_kind = _kind(node.left, allowed_names)
        for operator, right in zip(node.ops, node.comparators):
            right_kind = _kind(right, allowed_names)
            if isinstance(operator, (ast.Eq, ast.NotEq)):
                if left_kind != right_kind:
                    raise _InvalidExpression("comparison_sort_mismatch")
            elif isinstance(operator, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                if left_kind != "int" or right_kind != "int":
                    raise _InvalidExpression("ordered_comparison_on_boolean")
            else:
                raise _InvalidExpression("unsupported_comparison")
            left_kind = right_kind
        return "bool"
    if isinstance(node, ast.Call):
        # ``_acsl_to_py`` uses bool(...) only to encode <==>.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "bool"
            and len(node.args) == 1
            and not node.keywords
        ):
            if _kind(node.args[0], allowed_names) != "bool":
                raise _InvalidExpression("equivalence_on_integer")
            return "bool"
        raise _InvalidExpression("function_call")
    raise _InvalidExpression(f"unsupported_{type(node).__name__}")


def parse_scalar_invariant(expression: str, program: Program) -> ParseVerdict:
    """Check syntax, scalar types, labels, and program-variable scope."""
    expression = normalize_invariant(expression)
    if not expression:
        return ParseVerdict(False, "empty")
    if "/*" in expression or "*/" in expression or "//" in expression:
        return ParseVerdict(False, "comment")
    if any(token in expression for token in ("?", ":", "^", ";")):
        return ParseVerdict(False, "forbidden_token")
    if _FORBIDDEN_COMMAND.search(expression):
        return ParseVerdict(False, "forbidden_acsl_command")
    if _TYPE_CAST.search(expression):
        return ParseVerdict(False, "type_cast")

    at_names: set[str] = set()
    loop_entry_locals = {
        name
        for name, initializer in program.local_inits
        if _DIRECT_UNKNOWN_CALL.fullmatch(initializer)
    }

    def replace_at(match: re.Match[str]) -> str:
        variable, label = match.groups()
        if label == "Pre" and variable not in program.params:
            raise _InvalidExpression("pre_requires_parameter")
        if label == "LoopEntry" and variable not in loop_entry_locals:
            raise _InvalidExpression("loopentry_requires_unknown_local")
        name = f"__at_{label}_{variable}"
        at_names.add(name)
        return name

    try:
        without_at = _AT_CALL.sub(replace_at, expression)
    except _InvalidExpression as error:
        return ParseVerdict(False, str(error))
    if "\\" in without_at:
        return ParseVerdict(False, "invalid_at")
    if _FUNCTION_CALL.search(without_at):
        return ParseVerdict(False, "function_call")

    # Re-run the shared ACSL-to-Python translation after replacing labels with
    # ordinary identifiers.  This preserves implication/equivalence precedence.
    try:
        tree = ast.parse(_acsl_to_py(without_at).strip(), mode="eval")
        # ``_acsl_to_py`` aliases program variables that are Python keywords.
        allowed = (
            {_python_name(name) for name in program.pre_vars}
            | {
                _python_name(name)
                for name in integer_source_constants(program.source)
            }
            | at_names
            | {"True", "False"}
        )
        if _kind(tree, allowed) != "bool":
            return ParseVerdict(False, "not_a_proposition")
    except (SyntaxError, TypeError, ValueError, _InvalidExpression) as error:
        reason = str(error) if isinstance(error, _InvalidExpression) else "parse_error"
        return ParseVerdict(False, reason)
    return ParseVerdict(True)


def lightweight_syntax_filter(
    program: Program, invariants: Iterable[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    for invariant in invariants:
        normalized = normalize_invariant(invariant)
        verdict = parse_scalar_invariant(normalized, program)
        if verdict.valid:
            kept.append(normalized)
        else:
            dropped.append((normalized, verdict.reason))
    return kept, dropped
