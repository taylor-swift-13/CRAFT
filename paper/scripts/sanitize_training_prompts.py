#!/usr/bin/env python3
"""Build canonical, target-hidden LoopGym RL and SFT training datasets.

The archival inputs are never overwritten.  Both datasets are rebuilt with the
current prompt files and only supported scalar-integer, single-loop programs.
SFT answers are reduced to canonical invariant lines, statically scrubbed to the
deployed ACSL interface, conservatively deduplicated, optionally checked by the
same Frama-C/WP Houdini filter used by reward and inference, and capped at the
model-facing twenty-clause limit.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import pyarrow as pa
import pyarrow.parquet as pq
import z3


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rl_pipeline.common import prompts  # noqa: E402
from rl_pipeline.common.program import (  # noqa: E402
    Program,
    parse_program,
    strip_noncontract_comments,
    strip_postcondition,
)
from rl_pipeline.common.state import (  # noqa: E402
    MAX_INVARIANTS_PER_RESPONSE,
    dedup_normalized,
    extract_invariants,
    normalize_invariant,
)
from rl_pipeline.reward.filters import out_of_scope_ids  # noqa: E402


DEFAULT_RL = ROOT / "traindata" / "loopgym_rl_0803.parquet"
DEFAULT_SFT = ROOT / "traindata" / "loopgym_sft_0803.json"
DEFAULT_RL_OUTPUT = ROOT / "traindata" / "loopgym_rl_clean.parquet"
DEFAULT_SFT_OUTPUT = ROOT / "traindata" / "loopgym_sft_clean.json"
PROGRAM_MARKER = "Program:\n"

_FORBIDDEN_ACSL = re.compile(
    r"\\(?:old|result|forall|exists|lambda|let|sum|product|min|max|numof)\b"
)
_BOOLEAN_CONSTANT = re.compile(r"(?<![A-Za-z0-9_])(?:\\true|\\false|true|false)(?![A-Za-z0-9_])")
_FUNCTION_CALL = re.compile(r"(?<!\\)\b([A-Za-z_]\w*)\s*\(")
_AT_CALL = re.compile(
    r"\\at\(\s*([A-Za-z_]\w*)\s*,\s*(Pre|LoopEntry)\s*\)"
)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_POWER_CALL_START = re.compile(r"\bpower\s*\(")
_MAX_EXPANDED_POWER = 20
_C_INTEGER_CAST = re.compile(
    r"\(\s*(?:(?:unsigned|signed)\s+)?(?:char|short|int|long(?:\s+long)?)\s*\)"
)
_UNKNOWN_CALL = re.compile(r"\bunknown\w*\s*\(")
_DIRECT_UNKNOWN_CALL = re.compile(r"^\s*unknown\w*\s*\(\s*\)\s*$")


def _program_suffix(message: str) -> tuple[str, str]:
    if PROGRAM_MARKER not in message:
        raise ValueError("model-facing prompt has no 'Program:\\n' marker")
    return message.split(PROGRAM_MARKER, 1)


def _canonical_user(source: str) -> str:
    return prompts.GENERATE_PROMPT.format(program=source)


def _canonical_source(source: str) -> str:
    """Normalize only terminal whitespace so prompt reconstruction is idempotent."""
    return strip_postcondition(source).rstrip() + "\n"


def _is_target_hidden(source: str) -> bool:
    return strip_postcondition(source) == source


def _sanitize_visible_source(
    visible_source: str, full_source: str
) -> tuple[str, bool]:
    """Return target-hidden source, preferring matching visible formatting."""
    candidate = strip_postcondition(visible_source)
    authoritative = strip_postcondition(full_source)
    if candidate.rstrip() == authoritative.rstrip():
        return candidate, False
    if visible_source.endswith("\n") and not authoritative.endswith("\n"):
        authoritative += "\n"
    elif not visible_source.endswith("\n") and authoritative.endswith("\n"):
        authoritative = authoritative.rstrip("\n")
    return authoritative, True


def _parse_supported(source: str) -> Program:
    program = parse_program(source)
    if len(program.loops) != 1:
        raise ValueError("expected exactly one loop")
    return program


def _strip_outer_parentheses(expression: str) -> str:
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        encloses_all = True
        for index, char in enumerate(result):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(result) - 1:
                    encloses_all = False
                    break
        if not encloses_all or depth != 0:
            break
        result = result[1:-1].strip()
    return result


def _preloop_region(program: Program) -> str:
    """Return the selected function body prefix preceding its only loop."""
    signature = re.search(
        rf"\b{re.escape(program.func_name)}\s*\([^)]*\)\s*\{{",
        strip_noncontract_comments(program.source),
    )
    if signature is None:
        return ""
    opening = program.source.find("{", signature.start(), signature.end())
    return program.source[opening + 1 : program.loop.kw_start]


def _preloop_assignment_info(program: Program) -> tuple[dict[str, str], set[str]]:
    """Collect final unconditional assignments and variables with path-dependent writes.

    The benchmark prefix is intentionally simple.  Straight-line assignments can
    be substituted into ACSL.  A write nested in a pre-loop branch is conservatively
    opaque, so its entry value remains nameable only through ``LoopEntry``.
    """
    region = strip_noncontract_comments(_preloop_region(program))
    unconditional: dict[str, str] = {}
    opaque: set[str] = set()
    assignment = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*([^=;][^;]*);")
    for match in assignment.finditer(region):
        prefix = region[: match.start()]
        name = match.group(1)
        value = re.sub(r"\s+", " ", match.group(2)).strip()
        if prefix.count("{") == prefix.count("}"):
            unconditional[name] = value
        else:
            opaque.add(name)
    update = re.compile(
        r"(?:\b([A-Za-z_]\w*)\s*(?:\+=|-=|\*=|/=|%=|\+\+|--)|"
        r"(?:\+\+|--)\s*\b([A-Za-z_]\w*))"
    )
    for match in update.finditer(region):
        opaque.add(match.group(1) or match.group(2))
    return unconditional, opaque


def _loop_entry_resolver(program: Program):
    """Build exact entry-value expressions with the fewest necessary labels.

    Parameters and globals begin at ``Pre``.  Deterministic local initialization
    is recursively inlined.  Only a local assigned directly by an unknown call
    is retained as ``\\at(v,LoopEntry)``; unrepresentable values cause their
    containing clauses to be rejected.
    """
    assignments, opaque = _preloop_assignment_info(program)
    local_initializers = dict(program.local_inits)
    local_names = set(local_initializers)
    definitions: dict[str, str | None] = {
        variable: f"\\at({variable},Pre)" for variable in program.pre_vars
    }
    definitions.update(local_initializers)
    for variable, value in assignments.items():
        # ``Program.local_inits`` already incorporates the last unconditional
        # assignment to every local.  This extra map is needed for parameters
        # and globals overwritten before the loop.
        if variable in definitions and variable not in local_names:
            definitions[variable] = value
    cache: dict[str, tuple[str | None, bool]] = {}

    def resolve(variable: str, stack: tuple[str, ...] = ()) -> tuple[str | None, bool]:
        if variable in cache:
            return cache[variable]
        if variable in stack or variable in opaque:
            return None, False
        expression = definitions.get(variable)
        if expression is None or not str(expression).strip():
            return None, False
        expression = str(expression).strip()
        if expression == f"\\at({variable},Pre)":
            result = (expression, False)
            cache[variable] = result
            return result
        if _DIRECT_UNKNOWN_CALL.fullmatch(expression):
            if variable in local_names:
                result = (f"\\at({variable},LoopEntry)", True)
                cache[variable] = result
                return result
            return None, False
        if _UNKNOWN_CALL.search(expression):
            return None, False
        if _FUNCTION_CALL.search(expression):
            return None, False

        expression = _C_INTEGER_CAST.sub("", expression)
        unresolved = False
        depends_on_entry = False

        def replace_identifier(match: re.Match[str]) -> str:
            nonlocal unresolved, depends_on_entry
            name = match.group(0)
            if name not in definitions:
                if name in {"true", "false"}:
                    return name
                unresolved = True
                return name
            replacement, needs_entry = resolve(name, stack + (variable,))
            if replacement is None:
                unresolved = True
                return name
            depends_on_entry |= needs_entry
            return f"({replacement})"

        translated = _IDENTIFIER.sub(replace_identifier, expression)
        if unresolved or "?" in translated or ":" in translated:
            return None, False
        result = (normalize_invariant(translated), depends_on_entry)
        cache[variable] = result
        return result

    return resolve


def _minimize_loop_entry(
    invariant: str, program: Program
) -> tuple[str, Counter[str]]:
    """Rewrite every reconstructable ``LoopEntry`` reference in one clause."""
    resolve = _loop_entry_resolver(program)
    changes: Counter[str] = Counter()

    def replace_at(match: re.Match[str]) -> str:
        variable, label = match.groups()
        if label != "LoopEntry":
            return match.group(0)
        replacement, required = resolve(variable)
        if replacement is None:
            changes["loopentry_forbidden_unresolved"] += 1
            return match.group(0)
        if replacement == f"\\at({variable},LoopEntry)" and required:
            changes["loopentry_required_retained"] += 1
            return match.group(0)
        changes["loopentry_rewritten"] += 1
        if replacement == f"\\at({variable},Pre)":
            changes["loopentry_to_pre"] += 1
        else:
            changes["loopentry_initializer_inlined"] += 1
        return f"({replacement})"

    return normalize_invariant(_AT_CALL.sub(replace_at, invariant)), changes


def _unnecessary_loop_entry_references(invariant: str, program: Program) -> list[str]:
    """Return variables whose entry values can be reconstructed without their label."""
    resolve = _loop_entry_resolver(program)
    unnecessary: list[str] = []
    for variable, label in _AT_CALL.findall(invariant):
        if label != "LoopEntry":
            continue
        replacement, required = resolve(variable)
        if replacement != f"\\at({variable},LoopEntry)" or not required:
            unnecessary.append(variable)
    return unnecessary


def _matching_parenthesis(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_call_arguments(arguments: str) -> tuple[str, str] | None:
    depth = 0
    comma = None
    for index, char in enumerate(arguments):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            if comma is not None:
                return None
            comma = index
    if comma is None:
        return None
    return arguments[:comma].strip(), arguments[comma + 1:].strip()


def _rewrite_fixed_powers(expression: str) -> tuple[str, int]:
    """Expand ``power(base, constant)`` into bounded scalar multiplication.

    Symbolic exponents cannot be represented by a finite polynomial and are
    deliberately left for the helper-function rejection gate.  Processing the
    rightmost call first also handles nested fixed powers.
    """
    result = expression
    rewritten = 0
    while True:
        changed = False
        for match in reversed(list(_POWER_CALL_START.finditer(result))):
            opening = result.find("(", match.start(), match.end())
            closing = _matching_parenthesis(result, opening)
            if closing is None:
                continue
            arguments = _split_call_arguments(result[opening + 1:closing])
            if arguments is None:
                continue
            base, exponent_text = arguments
            exponent_text = _strip_outer_parentheses(exponent_text)
            if not re.fullmatch(r"\+?\d+", exponent_text):
                continue
            exponent = int(exponent_text)
            if exponent > _MAX_EXPANDED_POWER or not base:
                continue
            if exponent == 0:
                replacement = "1"
            elif exponent == 1:
                replacement = f"({base})"
            else:
                replacement = " * ".join(f"({base})" for _ in range(exponent))
            result = result[:match.start()] + replacement + result[closing + 1:]
            rewritten += 1
            changed = True
            break
        if not changed:
            return normalize_invariant(result), rewritten


def _split_top_level_operator(expression: str, operator: str) -> tuple[str, str] | None:
    depth = 0
    index = 0
    while index <= len(expression) - len(operator):
        char = expression[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and expression.startswith(operator, index):
            return expression[:index].strip(), expression[index + len(operator):].strip()
        index += 1
    return None


def _abstract_symbolic_powers(expression: str) -> tuple[str, dict[str, str]]:
    """Replace each remaining power call by a stable opaque SymPy symbol."""
    result = expression
    symbols: dict[str, str] = {}
    while True:
        matches = list(_POWER_CALL_START.finditer(result))
        if not matches:
            return result, symbols
        match = matches[-1]
        opening = result.find("(", match.start(), match.end())
        closing = _matching_parenthesis(result, opening)
        if closing is None:
            return result, symbols
        arguments = _split_call_arguments(result[opening + 1:closing])
        if arguments is None:
            return result, symbols
        base, exponent = arguments
        key = re.sub(r"\s+", "", f"power({base},{exponent})")
        symbol = f"PWR_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"
        symbols[symbol] = key
        result = result[:match.start()] + symbol + result[closing + 1:]


def _equation_context(clause: str):
    """Return (antecedent, equation text, opaque power map), if supported."""
    clause = _strip_outer_parentheses(normalize_invariant(clause))
    implication = _split_top_level_operator(clause, "==>")
    if implication is None:
        antecedent, equation = None, clause
    else:
        antecedent, equation = implication
        equation = _strip_outer_parentheses(equation)
    if any(token in equation for token in ("&&", "||", "<==>", "/", "%", "?", ":")):
        return None
    equality = _split_top_level_operator(equation, "==")
    if equality is None:
        return None
    left, right = equality
    abstracted, power_map = _abstract_symbolic_powers(f"({left}) - ({right})")
    if not power_map or "power(" in abstracted:
        return None
    return antecedent, abstracted, power_map


def _sympy_expression(text: str, power_map: dict[str, str]):
    import sympy

    at_symbols: dict[str, str] = {}

    def replace_at(match: re.Match[str]) -> str:
        variable, label = match.groups()
        symbol = f"AT_{variable}_{label}"
        at_symbols[symbol] = match.group(0)
        return symbol

    clean = _AT_CALL.sub(replace_at, text)
    clean = _C_INTEGER_CAST.sub("", clean)
    if "\\" in clean or "!" in clean:
        return None
    names = set(_IDENTIFIER.findall(clean))
    local_dict = {name: sympy.Symbol(name) for name in names}
    try:
        expression = sympy.sympify(clean, locals=local_dict, evaluate=True)
    except (SyntaxError, TypeError, ValueError, sympy.SympifyError):
        return None
    powers = {local_dict[name] for name in power_map if name in local_dict}
    if not powers:
        return None
    try:
        polynomial = sympy.Poly(expression, *sorted(powers, key=str))
    except sympy.PolynomialError:
        return None
    if polynomial.total_degree() > 1:
        return None
    return expression, powers, at_symbols


def _polynomial_text(expression, at_symbols: dict[str, str]) -> str | None:
    import sympy
    from sympy.printing.precedence import precedence
    from sympy.printing.str import StrPrinter

    class ScalarPolynomialPrinter(StrPrinter):
        def _print_Pow(self, value, rational=False):
            if value.exp.is_Integer and 0 <= int(value.exp) <= _MAX_EXPANDED_POWER:
                exponent = int(value.exp)
                if exponent == 0:
                    return "1"
                base = self.parenthesize(value.base, precedence(value), strict=False)
                return " * ".join(base for _ in range(exponent))
            return super()._print_Pow(value, rational=rational)

    simplified = sympy.factor(expression)
    text = ScalarPolynomialPrinter().doprint(simplified)
    if "**" in text or len(text) > 600:
        return None
    for symbol, original in sorted(at_symbols.items(), key=lambda item: -len(item[0])):
        text = re.sub(
            rf"\b{re.escape(symbol)}\b", lambda _match, value=original: value, text
        )
    return text


def _primitive_polynomial(expression):
    import sympy

    symbols = sorted(expression.free_symbols, key=str)
    if not symbols:
        return sympy.expand(expression)
    try:
        polynomial = sympy.Poly(sympy.expand(expression), *symbols)
    except sympy.PolynomialError:
        return sympy.factor(expression)
    _, primitive = polynomial.primitive()
    if primitive.LC().could_extract_minus_sign():
        primitive = -primitive
    return sympy.factor(primitive.as_expr())


def _is_polynomial_multiple(candidate, stronger) -> bool:
    import sympy

    symbols = sorted(candidate.free_symbols | stronger.free_symbols, key=str)
    if not symbols:
        return sympy.simplify(candidate - stronger) == 0
    try:
        quotient, remainder = sympy.div(
            sympy.Poly(sympy.expand(candidate), *symbols),
            sympy.Poly(sympy.expand(stronger), *symbols),
        )
    except (sympy.PolynomialError, ZeroDivisionError):
        return False
    return remainder.is_zero and not quotient.is_zero


def _has_nontrivial_product_factors(expression) -> bool:
    """Reject weak ``f * g == 0`` consequences produced by elimination.

    Cross-multiplying two equations with variable coefficients can produce a
    disjunctive product equality.  Such a clause is often verified only because
    an initialization-only factor is zero, so it teaches little about the loop
    state.  Irreducible polynomial relations remain eligible for WP filtering.
    """
    import sympy

    factors = [
        factor
        for factor in sympy.Mul.make_args(sympy.factor(expression))
        if not factor.is_Number
    ]
    return len(factors) > 1


def _derive_power_free_relations(invariants: Sequence[str]) -> list[dict[str, Any]]:
    """Eliminate a shared symbolic power from pairs of affine equalities."""
    import sympy

    equations = []
    for invariant in invariants:
        context = _equation_context(invariant)
        if context is None:
            continue
        antecedent, text, power_map = context
        parsed = _sympy_expression(text, power_map)
        if parsed is None:
            continue
        expression, powers, at_symbols = parsed
        equations.append(
            {
                "source": invariant,
                "antecedent": normalize_invariant(antecedent or ""),
                "expression": expression,
                "powers": powers,
                "at_symbols": at_symbols,
            }
        )

    candidates: list[dict[str, Any]] = []
    for left_index, left in enumerate(equations):
        for right in equations[left_index + 1:]:
            common = left["powers"] & right["powers"]
            if len(common) != 1 or left["powers"] != common or right["powers"] != common:
                continue
            left_guard, right_guard = left["antecedent"], right["antecedent"]
            if left_guard and right_guard and left_guard != right_guard:
                continue
            guard = left_guard or right_guard
            power = next(iter(common))
            left_coefficient = sympy.expand(left["expression"]).coeff(power)
            right_coefficient = sympy.expand(right["expression"]).coeff(power)
            if left_coefficient == 0 or right_coefficient == 0:
                continue
            eliminated = sympy.expand(
                right_coefficient * left["expression"]
                - left_coefficient * right["expression"]
            )
            eliminated = _primitive_polynomial(sympy.simplify(eliminated))
            if eliminated == 0 or eliminated.has(power):
                continue
            if _has_nontrivial_product_factors(eliminated):
                continue
            at_symbols = {**left["at_symbols"], **right["at_symbols"]}
            candidates.append(
                {
                    "expression": eliminated,
                    "guard": guard,
                    "at_symbols": at_symbols,
                    "sources": [left["source"], right["source"]],
                }
            )

    # Prefer the strongest polynomial in each implication context.  If one
    # candidate is merely a polynomial multiple of another (for example
    # z*((z-1)*x+1-y)==0), the multiple is redundant and weaker at z==0.
    kept: list[dict[str, Any]] = []
    candidates.sort(key=lambda item: (sympy.count_ops(item["expression"]), str(item["expression"])))
    for candidate in candidates:
        if any(
            previous["guard"] == candidate["guard"]
            and _is_polynomial_multiple(candidate["expression"], previous["expression"])
            for previous in kept
        ):
            continue
        expression = _polynomial_text(
            candidate["expression"], candidate["at_symbols"]
        )
        if not expression:
            continue
        clause = f"({expression}) == 0"
        if candidate["guard"]:
            clause = f"({candidate['guard']}) ==> ({clause})"
        kept.append({**candidate, "clause": normalize_invariant(clause)})

    unique = dedup_normalized(item["clause"] for item in kept)
    first_by_clause = {item["clause"]: item for item in kept}
    for item in first_by_clause.values():
        item.pop("expression", None)
        item.pop("guard", None)
        item.pop("at_symbols", None)
    return [first_by_clause[clause] for clause in unique]


def _remove_guarded_copies(invariants: Sequence[str]) -> tuple[list[str], int]:
    """Drop ``guard ==> P`` when the same answer already contains ``P``."""
    unconditional = {
        _strip_outer_parentheses(normalize_invariant(invariant))
        for invariant in invariants
        if _split_top_level_operator(
            _strip_outer_parentheses(normalize_invariant(invariant)), "==>"
        )
        is None
    }
    kept: list[str] = []
    removed = 0
    for invariant in invariants:
        normalized = _strip_outer_parentheses(normalize_invariant(invariant))
        implication = _split_top_level_operator(normalized, "==>")
        if (
            implication is not None
            and _strip_outer_parentheses(normalize_invariant(implication[1]))
            in unconditional
        ):
            removed += 1
            continue
        kept.append(invariant)
    return kept, removed


def _obvious_tautology(expression: str) -> bool:
    """Recognize only reflexive scalar comparisons; never guess algebraically."""
    expression = _strip_outer_parentheses(expression)
    match = re.fullmatch(r"(.+?)\s*(==|<=|>=|<==>)\s*(.+)", expression)
    if not match:
        return False
    left = _strip_outer_parentheses(match.group(1))
    right = _strip_outer_parentheses(match.group(3))
    return left == right


_LOGIC_TOKEN = re.compile(
    r"\s*("
    r"<==>|==>|&&|\|\||==|!=|<=|>=|<<|>>|"
    r"\\at|[A-Za-z_]\w*|\d+|"
    r"[()+\-*/%,!<>]"
    r")"
)


class _LogicParseError(ValueError):
    pass


class _LogicParser:
    """Parse the scalar ACSL subset exposed by the training prompt into Z3."""

    def __init__(self, expression: str):
        self.tokens: list[str] = []
        position = 0
        while position < len(expression):
            match = _LOGIC_TOKEN.match(expression, position)
            if not match:
                raise _LogicParseError(f"unsupported token at {position}")
            self.tokens.append(match.group(1))
            position = match.end()
        self.position = 0
        self.symbols: dict[str, z3.ArithRef] = {}

    def parse(self) -> z3.BoolRef:
        result = self._equivalence()
        if self.position != len(self.tokens) or not z3.is_bool(result):
            raise _LogicParseError("expected a complete Boolean expression")
        return result

    def _peek(self, *values: str) -> bool:
        return self.position < len(self.tokens) and self.tokens[self.position] in values

    def _take(self, value: str | None = None) -> str:
        if self.position >= len(self.tokens):
            raise _LogicParseError("unexpected end of expression")
        token = self.tokens[self.position]
        if value is not None and token != value:
            raise _LogicParseError(f"expected {value!r}, found {token!r}")
        self.position += 1
        return token

    @staticmethod
    def _boolean(value: z3.ExprRef) -> z3.BoolRef:
        if not z3.is_bool(value):
            raise _LogicParseError("logical operator applied to an integer")
        return value

    @staticmethod
    def _integer(value: z3.ExprRef) -> z3.ArithRef:
        if not z3.is_arith(value):
            raise _LogicParseError("arithmetic operator applied to a proposition")
        return value

    def _equivalence(self) -> z3.ExprRef:
        result = self._implication()
        while self._peek("<==>"):
            self._take()
            result = self._boolean(result) == self._boolean(self._implication())
        return result

    def _implication(self) -> z3.ExprRef:
        left = self._disjunction()
        if self._peek("==>"):
            self._take()
            return z3.Implies(self._boolean(left), self._boolean(self._implication()))
        return left

    def _disjunction(self) -> z3.ExprRef:
        values = [self._conjunction()]
        while self._peek("||"):
            self._take()
            values.append(self._conjunction())
        if len(values) == 1:
            return values[0]
        return z3.Or(*(self._boolean(value) for value in values))

    def _conjunction(self) -> z3.ExprRef:
        values = [self._equality()]
        while self._peek("&&"):
            self._take()
            values.append(self._equality())
        if len(values) == 1:
            return values[0]
        return z3.And(*(self._boolean(value) for value in values))

    def _equality(self) -> z3.ExprRef:
        result = self._relation()
        while self._peek("==", "!="):
            operator = self._take()
            right = self._relation()
            if z3.is_bool(result) != z3.is_bool(right):
                raise _LogicParseError("equality operands have different sorts")
            result = result == right if operator == "==" else result != right
        return result

    def _relation(self) -> z3.ExprRef:
        result = self._sum()
        while self._peek("<", "<=", ">", ">="):
            operator = self._take()
            left = self._integer(result)
            right = self._integer(self._sum())
            result = {
                "<": left < right,
                "<=": left <= right,
                ">": left > right,
                ">=": left >= right,
            }[operator]
        return result

    def _sum(self) -> z3.ExprRef:
        result = self._shift()
        while self._peek("+", "-"):
            operator = self._take()
            left = self._integer(result)
            right = self._integer(self._shift())
            result = left + right if operator == "+" else left - right
        return result

    def _shift(self) -> z3.ExprRef:
        result = self._product()
        while self._peek("<<", ">>"):
            # Integer bitshift is deliberately not modeled by the conservative
            # tautology checker; Frama-C/WP remains the authority for validity.
            raise _LogicParseError("bitshift is not modeled")
        return result

    def _product(self) -> z3.ExprRef:
        result = self._unary()
        while self._peek("*", "/", "%"):
            operator = self._take()
            if operator != "*":
                # Z3 and C/ACSL disagree on signed division and remainder.
                # Keep such clauses unless a simpler static rule catches them.
                raise _LogicParseError("division and remainder are not modeled")
            result = self._integer(result) * self._integer(self._unary())
        return result

    def _unary(self) -> z3.ExprRef:
        if self._peek("!"):
            self._take()
            return z3.Not(self._boolean(self._unary()))
        if self._peek("+"):
            self._take()
            return self._integer(self._unary())
        if self._peek("-"):
            self._take()
            return -self._integer(self._unary())
        return self._atom()

    def _atom(self) -> z3.ExprRef:
        if self._peek("("):
            self._take()
            result = self._equivalence()
            self._take(")")
            return result
        if self._peek("\\at"):
            self._take()
            self._take("(")
            variable = self._take()
            self._take(",")
            label = self._take()
            self._take(")")
            if not re.fullmatch(r"[A-Za-z_]\w*", variable) or label not in {
                "Pre",
                "LoopEntry",
            }:
                raise _LogicParseError("invalid \\at expression")
            return self._symbol(f"at__{label}__{variable}")
        token = self._take()
        if token.isdigit():
            return z3.IntVal(token)
        if re.fullmatch(r"[A-Za-z_]\w*", token):
            return self._symbol(token)
        raise _LogicParseError(f"unexpected token {token!r}")

    def _symbol(self, name: str) -> z3.ArithRef:
        if name not in self.symbols:
            self.symbols[name] = z3.Int(name)
        return self.symbols[name]


def _universally_true(expression: str) -> bool:
    """Prove a supported scalar clause valid; return False on any uncertainty."""
    if _obvious_tautology(expression):
        return True
    # Most atomic invariants are intentionally informative.  Entry-value
    # equalities are the exception: archived answers contain algebraic identities
    # such as ``x == x0 + (x - x0)`` that are not caught by reflexive spelling.
    if (
        "\\at(" not in expression
        and not any(operator in expression for operator in ("==>", "<==>", "||", "&&"))
    ):
        return False
    try:
        proposition = _LogicParser(expression).parse()
        simplified = z3.simplify(proposition)
        if z3.is_true(simplified):
            return True
        solver = z3.Solver()
        solver.set(timeout=50)
        solver.add(z3.Not(proposition))
        return solver.check() == z3.unsat
    except (TypeError, z3.Z3Exception, _LogicParseError):
        return False


def _constant_integer_bound(expression: str):
    expression = _strip_outer_parentheses(normalize_invariant(expression))
    direct = re.fullmatch(
        r"([A-Za-z_]\w*)\s*(>=|>|<=|<)\s*([+-]?\d+)", expression
    )
    if direct:
        variable, operator, number = direct.groups()
    else:
        reverse = re.fullmatch(
            r"([+-]?\d+)\s*(<=|<|>=|>)\s*([A-Za-z_]\w*)", expression
        )
        if not reverse:
            return None
        number, operator, variable = reverse.groups()
        operator = {"<=": ">=", "<": ">", ">=": "<=", ">": "<"}[operator]
    value = int(number)
    if operator == ">":
        return variable, "lower", value + 1
    if operator == ">=":
        return variable, "lower", value
    if operator == "<":
        return variable, "upper", value - 1
    return variable, "upper", value


def _remove_subsumed_constant_bounds(invariants: Sequence[str]) -> tuple[list[str], int]:
    """Drop only integer bounds logically dominated by another retained bound."""
    groups: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for index, invariant in enumerate(invariants):
        bound = _constant_integer_bound(invariant)
        if bound is None:
            continue
        variable, direction, value = bound
        groups.setdefault((variable, direction), []).append((index, value))
    dropped: set[int] = set()
    for (_variable, direction), entries in groups.items():
        strongest = (
            max(value for _, value in entries)
            if direction == "lower"
            else min(value for _, value in entries)
        )
        keeper = next(index for index, value in entries if value == strongest)
        dropped.update(
            index for index, _value in entries if index != keeper
        )
    return [value for index, value in enumerate(invariants) if index not in dropped], len(dropped)


def _rejection_reason(invariant: str, program: Program) -> str | None:
    """Return the first reason a clause violates the deployed prompt/interface."""
    clause = normalize_invariant(invariant)
    if not clause:
        return "empty"
    if "?" in clause or ":" in clause:
        return "ternary"
    if "^" in clause:
        return "xor"
    if _BOOLEAN_CONSTANT.search(clause):
        return "boolean_constant"
    if _FORBIDDEN_ACSL.search(clause):
        return "unsupported_acsl"

    # Every \at occurrence must be exactly \at(variable, Pre|LoopEntry).
    without_at = _AT_CALL.sub(" ", clause)
    if "\\at" in without_at:
        return "invalid_at"
    for variable, label in _AT_CALL.findall(clause):
        if variable not in program.pre_vars:
            return "out_of_scope"
        local_names = {name for name, _ in program.local_inits}
        if label == "Pre" and variable in local_names:
            return "invalid_pre_label"
    if _unnecessary_loop_entry_references(clause, program):
        return "unnecessary_loopentry"

    # The current prompt exposes no user-defined or helper function calls.
    if _FUNCTION_CALL.search(without_at):
        return "helper_function"
    if out_of_scope_ids(clause, program.pre_vars):
        return "out_of_scope"
    if _universally_true(clause):
        return "tautology"

    identifiers = set(_IDENTIFIER.findall(_AT_CALL.sub(" ", clause)))
    if not identifiers.intersection(program.pre_vars):
        return "constant_only"
    return None


def _static_clean_invariants(
    answer: str, program: Program
) -> tuple[
    list[str], Counter[str], Counter[str], list[dict[str, Any]], list[dict[str, Any]]
]:
    reasons: Counter[str] = Counter()
    transformations: Counter[str] = Counter()
    extracted = extract_invariants(answer)
    reasons["unparsed_or_non_invariant_lines"] = sum(
        1 for line in answer.splitlines() if line.strip()
        and not re.fullmatch(r"\s*loop\s+invariant\s+.+;\s*", line)
    )
    accepted: list[str] = []
    rewritten_clauses: list[str] = []
    power_decisions: list[dict[str, Any]] = []
    for invariant in extracted:
        minimized, loopentry_changes = _minimize_loop_entry(invariant, program)
        transformations.update(loopentry_changes)
        rewritten, rewrite_count = _rewrite_fixed_powers(minimized)
        if rewrite_count:
            transformations["fixed_power_calls_expanded"] += rewrite_count
            transformations["clauses_with_fixed_power_expansion"] += 1
        rewritten_clauses.append(rewritten)
        reason = _rejection_reason(rewritten, program)
        if "power(" in invariant:
            power_decisions.append(
                {
                    "original": invariant,
                    "rewritten": rewritten,
                    "fixed_calls_expanded": rewrite_count,
                    "remaining_symbolic_power": "power(" in rewritten,
                    "static_decision": "candidate" if reason is None else "removed",
                    "static_reason": reason,
                }
            )
        if reason is None:
            accepted.append(rewritten)
        else:
            reasons[reason] += 1
    derived = _derive_power_free_relations(rewritten_clauses)
    for item in derived:
        reason = _rejection_reason(item["clause"], program)
        if reason is None:
            accepted.append(item["clause"])
            transformations["symbolic_power_relations_derived"] += 1
        else:
            item["rejected_before_frama_c"] = reason
    deduplicated = dedup_normalized(accepted)
    reasons["duplicate"] += len(accepted) - len(deduplicated)
    deduplicated, guarded_copies = _remove_guarded_copies(deduplicated)
    reasons["guarded_copy"] += guarded_copies
    deduplicated, subsumed = _remove_subsumed_constant_bounds(deduplicated)
    reasons["subsumed_bound"] += subsumed
    retained = set(deduplicated)
    for decision in power_decisions:
        if decision["static_decision"] == "candidate":
            decision["static_retained"] = decision["rewritten"] in retained
            if not decision["static_retained"]:
                decision["static_decision"] = "removed"
                decision["static_reason"] = "duplicate_or_subsumed"
    for relation in derived:
        relation["static_retained"] = relation["clause"] in retained
    return deduplicated, reasons, transformations, derived, power_decisions


def _verify_job(job: tuple[int, str, list[str]]) -> tuple[int, list[str], str | None]:
    index, source, invariants = job
    try:
        from rl_pipeline.reward.filters import HoudiniFilter

        program = _parse_supported(source)
        survivors = HoudiniFilter().filter(program, 0, invariants, None)
        return index, dedup_normalized(survivors), None
    except Exception as error:  # fail closed; caller records and drops the row
        return index, [], f"{type(error).__name__}: {error}"


def _kernel_syntax_job(job: tuple[int, str]) -> tuple[int, bool, str | None]:
    import subprocess

    index, source = job
    handle, temporary = tempfile.mkstemp(prefix="rlsyntax_", suffix=".c")
    os.close(handle)
    path = Path(temporary)
    try:
        path.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [
                "frama-c",
                "-kernel-warn-key",
                "annot-error=abort",
                "-print",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        valid = completed.returncode == 0 and "user error" not in output.lower()
        diagnostic = None if valid else re.sub(r"\s+", " ", output).strip()[:300]
        return index, valid, diagnostic
    except Exception as error:
        return index, False, f"{type(error).__name__}: {error}"
    finally:
        path.unlink(missing_ok=True)


def _kernel_syntax_results(
    sources: Sequence[str], jobs_count: int
) -> dict[int, tuple[bool, str | None]]:
    jobs = list(enumerate(sources))
    if jobs_count <= 1:
        return {
            index: (valid, error)
            for index, valid, error in map(_kernel_syntax_job, jobs)
        }
    results: dict[int, tuple[bool, str | None]] = {}
    with ProcessPoolExecutor(max_workers=jobs_count) as executor:
        futures = [executor.submit(_kernel_syntax_job, job) for job in jobs]
        for completed, future in enumerate(as_completed(futures), 1):
            index, valid, error = future.result()
            results[index] = (valid, error)
            if completed % 500 == 0 or completed == len(jobs):
                print(
                    f"Frama-C syntax checked {completed}/{len(jobs)} unique RL programs",
                    file=sys.stderr,
                    flush=True,
                )
    return results


def _verify_jobs(
    jobs: Sequence[tuple[int, str, list[str]]], jobs_count: int
) -> dict[int, tuple[list[str], str | None]]:
    if jobs_count <= 1:
        results: Iterable[tuple[int, list[str], str | None]] = map(_verify_job, jobs)
        return {index: (survivors, error) for index, survivors, error in results}
    with ProcessPoolExecutor(max_workers=jobs_count) as executor:
        futures = [executor.submit(_verify_job, job) for job in jobs]
        verified: dict[int, tuple[list[str], str | None]] = {}
        for completed, future in enumerate(as_completed(futures), 1):
            index, survivors, error = future.result()
            verified[index] = (survivors, error)
            if completed % 100 == 0 or completed == len(jobs):
                print(
                    f"Frama-C cleaned {completed}/{len(jobs)} SFT records",
                    file=sys.stderr,
                    flush=True,
                )
        return verified


def sanitize_rl_rows(
    rows: Sequence[dict[str, Any]],
    *,
    verify_syntax: bool = False,
    jobs_count: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    stats: dict[str, Any] = {
        "input_rows": len(rows),
        "output_rows": 0,
        "modified_prompts": 0,
        "reconstructed_from_archival_source": 0,
        "dropped_programs": {},
        "output_prompts_with_target": 0,
        "output_prompt_mismatches": 0,
        "frama_c_syntax_checked_unique_programs": 0,
        "frama_c_syntax_invalid_unique_programs": 0,
        "dropped_syntax_rows": 0,
        "syntax_failures": [],
    }
    canonical_system = prompts.system_prompt()
    for original in rows:
        row = copy.deepcopy(original)
        try:
            full_source = row["reward_model"]["ground_truth"]["raw_code"]
            user_turns = [turn for turn in row["prompt"] if turn["role"] == "user"]
            if len(user_turns) != 1:
                raise ValueError(f"expected one user turn, found {len(user_turns)}")
            _, old_source = _program_suffix(user_turns[0]["content"])
            clean_source, reconstructed = _sanitize_visible_source(old_source, full_source)
            clean_source = _canonical_source(clean_source)
            _parse_supported(clean_source)
        except (KeyError, TypeError, ValueError) as error:
            reasons[str(error)] += 1
            continue

        canonical_prompt = [
            {"role": "system", "content": canonical_system},
            {"role": "user", "content": _canonical_user(clean_source)},
        ]
        stats["modified_prompts"] += int(canonical_prompt != row["prompt"])
        stats["reconstructed_from_archival_source"] += int(reconstructed)
        row["prompt"] = canonical_prompt
        stats["output_prompts_with_target"] += int(not _is_target_hidden(clean_source))
        stats["output_prompt_mismatches"] += int(
            canonical_prompt[0]["content"] != canonical_system
            or canonical_prompt[1]["content"] != _canonical_user(clean_source)
        )
        sanitized.append(row)

    if verify_syntax:
        if not shutil_which("frama-c"):
            raise RuntimeError(
                "--verify-rl-syntax requires frama-c on PATH; initialize the opam switch"
            )
        sources = [
            row["prompt"][1]["content"].split(PROGRAM_MARKER, 1)[1]
            for row in sanitized
        ]
        unique_sources = list(dict.fromkeys(sources))
        source_index = {source: index for index, source in enumerate(unique_sources)}
        syntax = _kernel_syntax_results(unique_sources, jobs_count)
        invalid = {
            source
            for source, index in source_index.items()
            if not syntax[index][0]
        }
        failure_examples = []
        for source in invalid:
            index = source_index[source]
            affected = [
                (row.get("extra_info") or {}).get("file_id")
                for row, row_source in zip(sanitized, sources)
                if row_source == source
            ]
            failure_examples.append(
                {
                    "file_ids": affected[:10],
                    "rows": len(affected),
                    "diagnostic": syntax[index][1],
                }
            )
        before = len(sanitized)
        sanitized = [
            row for row, source in zip(sanitized, sources) if source not in invalid
        ]
        stats["frama_c_syntax_checked_unique_programs"] = len(unique_sources)
        stats["frama_c_syntax_invalid_unique_programs"] = len(invalid)
        stats["dropped_syntax_rows"] = before - len(sanitized)
        stats["syntax_failures"] = failure_examples
    stats["output_rows"] = len(sanitized)
    stats["dropped_programs"] = dict(sorted(reasons.items()))
    return sanitized, stats


def sanitize_sft_records(
    records: Sequence[dict[str, Any]],
    *,
    verify: bool = False,
    jobs_count: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prepared: list[tuple[int, dict[str, Any], str, list[str]]] = []
    power_audit: list[dict[str, Any]] = []
    program_errors: Counter[str] = Counter()
    removed: Counter[str] = Counter()
    transformed: Counter[str] = Counter()
    stats: dict[str, Any] = {
        "input_rows": len(records),
        "output_rows": 0,
        "modified_prompts": 0,
        "modified_answers": 0,
        "dropped_programs": {},
        "dropped_empty_answers": 0,
        "frama_c_errors": 0,
        "clauses_before": 0,
        "clauses_after": 0,
        "removed_clauses": {},
        "transformations": {},
        "output_prompts_with_target": 0,
        "output_prompt_mismatches": 0,
        "output_answer_violations": 0,
    }
    canonical_system = prompts.system_prompt()

    for index, original in enumerate(records):
        record = copy.deepcopy(original)
        try:
            human_turns = [
                turn for turn in record["conversations"] if turn["from"] == "human"
            ]
            assistant_turns = [
                turn for turn in record["conversations"] if turn["from"] == "gpt"
            ]
            if len(human_turns) != 1 or len(assistant_turns) != 1:
                raise ValueError("expected one human and one gpt turn")
            _, old_source = _program_suffix(human_turns[0]["value"])
            clean_source = _canonical_source(old_source)
            program = _parse_supported(clean_source)
        except (KeyError, TypeError, ValueError) as error:
            program_errors[str(error)] += 1
            continue

        old_answer = assistant_turns[0]["value"]
        (
            invariants,
            row_removed,
            row_transformed,
            derived,
            power_decisions,
        ) = _static_clean_invariants(old_answer, program)
        removed.update(row_removed)
        transformed.update(row_transformed)
        if "power(" in old_answer:
            power_audit.append(
                {
                    "row": index,
                    "program": clean_source,
                    "power_clauses": power_decisions,
                    "derived_relations": derived,
                    "remaining_power_clauses_removed": row_removed.get(
                        "helper_function", 0
                    ),
                    "fixed_power_calls_expanded": row_transformed.get(
                        "fixed_power_calls_expanded", 0
                    ),
                }
            )
        stats["clauses_before"] += len(extract_invariants(old_answer))
        canonical_human = _canonical_user(clean_source)
        canonical_conversation = [
            {"from": "system", "value": canonical_system},
            {"from": "human", "value": canonical_human},
            {"from": "gpt", "value": old_answer},
        ]
        stats["modified_prompts"] += int(
            record.get("conversations", [])[:-1] != canonical_conversation[:-1]
        )
        record["conversations"] = canonical_conversation
        stats["output_prompts_with_target"] += int(not _is_target_hidden(clean_source))
        prepared.append((index, record, clean_source, invariants))

    verified: dict[int, tuple[list[str], str | None]] = {}
    if verify:
        if not shutil_which("frama-c"):
            raise RuntimeError(
                "--verify-sft requires frama-c on PATH; initialize the opam switch"
            )
        jobs = [(index, source, invariants) for index, _, source, invariants in prepared if invariants]
        verified = _verify_jobs(jobs, jobs_count)

    sanitized: list[dict[str, Any]] = []
    power_audit_by_row = {item["row"]: item for item in power_audit}
    for index, record, clean_source, static_invariants in prepared:
        invariants = static_invariants
        if verify and invariants:
            survivors, error = verified[index]
            stats["frama_c_errors"] += int(error is not None)
            removed["frama_c_rejected"] += len(invariants) - len(survivors)
            invariants = survivors
        audit = power_audit_by_row.get(index)
        if audit is not None:
            survivor_set = set(invariants)
            for decision in audit["power_clauses"]:
                candidate = decision["rewritten"]
                decision["frama_c_survived"] = bool(
                    decision.get("static_retained", False)
                    and candidate in survivor_set
                )
            for relation in audit["derived_relations"]:
                relation["frama_c_survived"] = bool(
                    relation.get("static_retained", False)
                    and relation["clause"] in survivor_set
                )
        if len(invariants) > MAX_INVARIANTS_PER_RESPONSE:
            removed["over_cap"] += len(invariants) - MAX_INVARIANTS_PER_RESPONSE
            invariants = invariants[:MAX_INVARIANTS_PER_RESPONSE]
        if audit is not None:
            final_set = set(invariants)
            for decision in audit["power_clauses"]:
                decision["final_retained"] = bool(
                    decision.get("frama_c_survived", False)
                    and decision["rewritten"] in final_set
                )
            for relation in audit["derived_relations"]:
                relation["final_retained"] = bool(
                    relation.get("frama_c_survived", False)
                    and relation["clause"] in final_set
                )
        if not invariants:
            stats["dropped_empty_answers"] += 1
            continue

        answer = "\n".join(f"loop invariant {clause};" for clause in invariants)
        old_answer = record["conversations"][-1]["value"]
        stats["modified_answers"] += int(answer != old_answer)
        record["conversations"][-1]["value"] = answer
        program = _parse_supported(clean_source)
        stats["output_answer_violations"] += sum(
            _rejection_reason(clause, program) is not None for clause in invariants
        )
        stats["output_prompt_mismatches"] += int(
            record["conversations"][0]["value"] != canonical_system
            or record["conversations"][1]["value"] != _canonical_user(clean_source)
        )
        stats["clauses_after"] += len(invariants)
        sanitized.append(record)

    stats["output_rows"] = len(sanitized)
    stats["dropped_programs"] = dict(sorted(program_errors.items()))
    stats["removed_clauses"] = dict(sorted((key, value) for key, value in removed.items() if value))
    stats["transformations"] = dict(
        sorted((key, value) for key, value in transformed.items() if value)
    )
    stats["_power_audit"] = power_audit
    return sanitized, stats


def shutil_which(command: str) -> str | None:
    # Local wrapper keeps the import surface small for forked verification jobs.
    import shutil

    return shutil.which(command)


def _safe_output(input_path: Path, output_path: Path | None) -> Path | None:
    if output_path is None:
        return None
    if input_path.resolve() == output_path.resolve():
        raise ValueError(f"refusing to overwrite input dataset: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def _atomic_parquet(rows: Sequence[dict[str, Any]], schema: pa.Schema, output: Path) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        pq.write_table(pa.Table.from_pylist(list(rows), schema=schema), temporary_path)
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_json(value: Any, output: Path) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rl-input", type=Path, default=DEFAULT_RL)
    parser.add_argument("--sft-input", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--rl-output", type=Path, default=DEFAULT_RL_OUTPUT)
    parser.add_argument("--sft-output", type=Path, default=DEFAULT_SFT_OUTPUT)
    parser.add_argument(
        "--report-output",
        type=Path,
        default=ROOT / "paper" / "artifacts" / "training_sanitation.json",
    )
    parser.add_argument(
        "--power-audit-output",
        type=Path,
        default=ROOT / "paper" / "artifacts" / "power_rewrite_audit.json",
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--verify-sft", action="store_true")
    parser.add_argument("--verify-rl-syntax", action="store_true")
    parser.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 1))
    parser.add_argument(
        "--rl-syntax-jobs", type=int, default=min(32, os.cpu_count() or 1)
    )
    parser.add_argument(
        "--wp-timeout",
        type=int,
        default=5,
        help="Frama-C/WP prover timeout per obligation during SFT cleaning",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.rl_syntax_jobs < 1:
        parser.error("--rl-syntax-jobs must be positive")
    if args.wp_timeout < 1:
        parser.error("--wp-timeout must be positive")
    missing_inputs = [
        f"{name}={path}"
        for name, path in (
            ("--rl-input", args.rl_input),
            ("--sft-input", args.sft_input),
        )
        if not path.is_file()
    ]
    if missing_inputs:
        parser.error(
            "missing archival input(s): "
            + ", ".join(missing_inputs)
            + "; pass the original archive paths explicitly, or use the clean "
            "artifacts as explicit inputs for a fixed-point check"
        )
    if args.verify_sft:
        os.environ["LOOPGYM_WP_TIMEOUT"] = str(args.wp_timeout)

    rl_output = None if args.check_only else _safe_output(args.rl_input, args.rl_output)
    sft_output = None if args.check_only else _safe_output(args.sft_input, args.sft_output)
    rl_table = pq.read_table(args.rl_input)
    rl_rows, rl_stats = sanitize_rl_rows(
        rl_table.to_pylist(),
        verify_syntax=args.verify_rl_syntax,
        jobs_count=args.rl_syntax_jobs,
    )
    sft_records = json.loads(args.sft_input.read_text(encoding="utf-8"))
    clean_sft, sft_stats = sanitize_sft_records(
        sft_records, verify=args.verify_sft, jobs_count=args.jobs
    )
    power_audit = sft_stats.pop("_power_audit", [])
    sft_stats["power_audit_rows"] = len(power_audit)

    outputs: dict[str, dict[str, str]] = {}
    if rl_output is not None:
        _atomic_parquet(rl_rows, rl_table.schema, rl_output)
        outputs["rl"] = {"path": str(rl_output), "sha256": _sha256(rl_output)}
    if sft_output is not None:
        _atomic_json(clean_sft, sft_output)
        outputs["sft"] = {"path": str(sft_output), "sha256": _sha256(sft_output)}

    report = {
        "schema_version": 4,
        "mode": "write" if outputs else "check_only",
        "frama_c_verified_sft": args.verify_sft,
        "frama_c_verified_rl_syntax": args.verify_rl_syntax,
        "inputs": {
            "rl": {"path": str(args.rl_input), "sha256": _sha256(args.rl_input)},
            "sft": {"path": str(args.sft_input), "sha256": _sha256(args.sft_input)},
        },
        "canonical_prompt_sha256": hashlib.sha256(
            prompts.system_prompt().encode("utf-8")
        ).hexdigest(),
        "rl": rl_stats,
        "sft": sft_stats,
        "outputs": outputs,
    }
    if args.report_output is not None:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(report, args.report_output)
    if args.power_audit_output is not None:
        args.power_audit_output.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            {
                "schema_version": 1,
                "policy": (
                    "expand fixed exponents; derive power-free polynomial relations "
                    "by eliminating shared symbolic powers; reject reducible product "
                    "equalities and guarded copies of unconditional conclusions; "
                    "retain only Frama-C/WP Houdini survivors"
                ),
                "rows": power_audit,
            },
            args.power_audit_output,
        )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
