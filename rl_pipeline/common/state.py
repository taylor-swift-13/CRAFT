"""
State = a variable valuation at the loop entry (loop head).

  State.vars : Dict[str,int]   current values of loop-entry variables
  State.pre        : Dict[str,int]   function-entry values (for \\at(v,Pre))
  State.loop_entry : Dict[str,int]   first loop-head values (for
                                     \\at(v,LoopEntry))

Plus a safe evaluator for ACSL-ish boolean predicates (invariants, guards,
postconditions) at a given state.  We convert the ACSL expression to a Python
expression and eval it in a locked-down namespace.  Integer semantics: C-style
`/` and `%` (truncation toward zero) — Python's floor semantics differ on
negatives (-7/2 is -3 in C but -4 under floor), and the sampled states ARE
C-executed values, so evaluating with floor semantics would wrongly filter
honest division/modulo invariants.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Dict, Hashable, List, Optional


@dataclass(frozen=True)
class State:
    vars: Dict[str, int]
    pre: Dict[str, int] = field(default_factory=dict)
    loop_entry: Dict[str, int] = field(default_factory=dict)
    # trace coordinates (run index, loop-head iteration) — metadata only, NOT part
    # of identity: used by the sampler to tell which states have their local trace
    # window sampled (perturbation-base density check).  -1 = synthetic/unknown.
    run: int = -1
    it: int = -1

    def key(self) -> tuple:
        return (
            self.vars_key()
            + (("__pre__",),)
            + tuple(sorted(self.pre.items()))
            + (("__loop_entry__",),)
            + tuple(sorted(self.loop_entry.items()))
        )

    def vars_key(self) -> tuple:
        """Reachability/identity key over the loop-entry valuation (ignores pre)."""
        return tuple(sorted(self.vars.items()))

    def __hash__(self):
        return hash(self.key())

    def render(self) -> str:
        current = " && ".join(f"{k} == {v}" for k, v in sorted(self.vars.items()))
        parts = [current]
        if self.pre:
            initial = " && ".join(
                f"{k} == {v}" for k, v in sorted(self.pre.items())
            )
            parts.append(f"Pre: {initial}")
        if self.loop_entry:
            entry = " && ".join(
                f"{k} == {v}" for k, v in sorted(self.loop_entry.items())
            )
            parts.append(f"LoopEntry: {entry}")
        return "; ".join(parts)


_INV_RE = re.compile(r"loop\s+invariant\s+([^;]+);")
MAX_INVARIANTS_PER_RESPONSE = 20


def normalize_invariant(inv: str) -> str:
    """Strip `loop invariant` prefix / trailing `;` and collapse whitespace."""
    s = inv.strip()
    s = re.sub(r"^loop\s+invariant\s+", "", s)
    if s.endswith(";"):
        s = s[:-1]
    return re.sub(r"\s+", " ", s).strip()


def extract_invariants(
    text: str, max_invariants: Optional[int] = None
) -> List[str]:
    """Pull `loop invariant <expr>;` texts (whitespace-normalized) out of annotated
    code or raw LLM output.

    ``max_invariants`` caps model responses without changing callers that parse
    fully annotated programs (which need the complete invariant count).
    """
    if not text:
        return []
    invariants = [
        re.sub(r"\s+", " ", match.group(1)).strip()
        for match in _INV_RE.finditer(text)
    ]
    if max_invariants is not None:
        if max_invariants < 0:
            raise ValueError("max_invariants must be non-negative")
        return invariants[:max_invariants]
    return invariants


def dedup_normalized(invariants):
    """Normalize and conservatively de-duplicate integer invariants.

    The first spelling is preserved for Frama-C and user-facing diagnostics;
    only the membership key is canonicalized.  Unsupported expressions fall
    back to their whitespace-normalized spelling.
    """
    out, seen = [], set()
    for inv in invariants:
        c = normalize_invariant(inv)
        key = invariant_dedup_key(c)
        if c and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _split_top_level(expr: str, sep: str) -> List[str]:
    parts, buf, depth = [], "", 0
    i, n, m = 0, len(expr), len(sep)
    while i < n:
        c = expr[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if depth == 0 and expr[i:i + m] == sep:
            parts.append(buf)
            buf = ""
            i += m
            continue
        buf += c
        i += 1
    parts.append(buf)
    return parts


def _has_outer_parens(expr: str) -> bool:
    if len(expr) < 2 or expr[0] != "(" or expr[-1] != ")":
        return False
    depth = 0
    for index, char in enumerate(expr):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(expr) - 1:
                return False
    return depth == 0


def _translate_logic(expr: str) -> str:
    """Recursively translate ACSL boolean operators with their precedence."""
    s = expr.strip()
    if _has_outer_parens(s):
        return f"({_translate_logic(s[1:-1])})"

    for operator, python_operator in (("<==>", None), ("==>", None),
                                      ("||", "or"), ("&&", "and")):
        parts = _split_top_level(s, operator)
        if len(parts) == 1:
            continue
        if operator == "<==>":
            translated = [_translate_logic(part) for part in parts]
            result = translated[0]
            for part in translated[1:]:
                result = f"(bool({result}) == bool({part}))"
            return result
        if operator == "==>":
            result = _translate_logic(parts[-1])
            for part in reversed(parts[:-1]):
                result = f"((not ({_translate_logic(part)})) or ({result}))"
            return result
        return f" {python_operator} ".join(
            f"({_translate_logic(part)})" for part in parts
        )

    if s.startswith("!") and not s.startswith("!="):
        return f"(not ({_translate_logic(s[1:])}))"

    # Translate boolean expressions nested inside otherwise atomic parentheses.
    out, index = [], 0
    while index < len(s):
        if s[index] != "(":
            out.append(s[index])
            index += 1
            continue
        depth, end = 1, index + 1
        while end < len(s) and depth:
            if s[end] == "(":
                depth += 1
            elif s[end] == ")":
                depth -= 1
            end += 1
        if depth:
            return s
        out.append("(" + _translate_logic(s[index + 1:end - 1]) + ")")
        index = end
    return "".join(out)


def _acsl_to_py(expr: str) -> str:
    """Convert an ACSL boolean expression to a Python expression string."""
    s = re.sub(r"\\true\b", "True", expr)
    s = re.sub(r"\\false\b", "False", s)
    s = re.sub(r"\\at\(\s*(\w+)\s*,\s*Pre\s*\)", r"\1__PRE__", s)
    s = re.sub(
        r"\\at\(\s*(\w+)\s*,\s*LoopEntry\s*\)",
        r"\1__LOOP_ENTRY__",
        s,
    )
    s = re.sub(r"\b(\w+)@pre\b", r"\1__PRE__", s)
    s = _translate_logic(s)
    s = re.sub(r"(?<![<>=!])=(?![=])", "==", s)
    return re.sub(r"(?<![/])/(?![/])", "//", s)


class _UnsupportedCanonicalNode(ValueError):
    """Raised when an expression is outside the conservative dedup subset."""


def _key_order(value: Hashable) -> str:
    """Stable total order for nested canonical-key tuples."""
    return repr(value)


def _ordered_pair(left: Hashable, right: Hashable):
    if _key_order(right) < _key_order(left):
        return right, left
    return left, right


def _constant_is(key: Hashable, value: int) -> bool:
    return key == ("constant", "int", value)


def _canonical_compare(operator, left: Hashable, right: Hashable):
    if isinstance(operator, ast.Gt):
        return ("compare", "lt", right, left)
    if isinstance(operator, ast.GtE):
        return ("compare", "le", right, left)
    if isinstance(operator, ast.Lt):
        return ("compare", "lt", left, right)
    if isinstance(operator, ast.LtE):
        return ("compare", "le", left, right)
    if isinstance(operator, ast.Eq):
        left, right = _ordered_pair(left, right)
        return ("compare", "eq", left, right)
    if isinstance(operator, ast.NotEq):
        left, right = _ordered_pair(left, right)
        return ("compare", "ne", left, right)
    raise _UnsupportedCanonicalNode(type(operator).__name__)


def _negate_canonical_key(key: Hashable):
    if isinstance(key, tuple) and key and key[0] == "not":
        return key[1]
    if not (isinstance(key, tuple) and len(key) == 4 and key[0] == "compare"):
        return ("not", key)
    _, operator, left, right = key
    if operator == "lt":
        return ("compare", "le", right, left)
    if operator == "le":
        return ("compare", "lt", right, left)
    if operator == "eq":
        return ("compare", "ne", left, right)
    if operator == "ne":
        return ("compare", "eq", left, right)
    raise _UnsupportedCanonicalNode(operator)


def _canonical_ast_key(node: ast.AST) -> Hashable:
    """Build a solver-free key using only semantics-safe integer rewrites."""
    if isinstance(node, ast.Expression):
        return _canonical_ast_key(node.body)
    if isinstance(node, ast.Name):
        return ("name", node.id)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return ("constant", "bool", node.value)
        if isinstance(node.value, int):
            return ("constant", "int", node.value)
        raise _UnsupportedCanonicalNode(type(node.value).__name__)
    if isinstance(node, ast.UnaryOp):
        operand = _canonical_ast_key(node.operand)
        if isinstance(node.op, ast.Not):
            return _negate_canonical_key(operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return ("unary", "neg", operand)
        raise _UnsupportedCanonicalNode(type(node.op).__name__)
    if isinstance(node, ast.BinOp):
        left = _canonical_ast_key(node.left)
        right = _canonical_ast_key(node.right)
        if isinstance(node.op, ast.Add):
            if _constant_is(left, 0):
                return right
            if _constant_is(right, 0):
                return left
            left, right = _ordered_pair(left, right)
            return ("binary", "add", left, right)
        if isinstance(node.op, ast.Mult):
            if _constant_is(left, 1):
                return right
            if _constant_is(right, 1):
                return left
            left, right = _ordered_pair(left, right)
            return ("binary", "mul", left, right)
        if isinstance(node.op, ast.Sub):
            if _constant_is(right, 0):
                return left
            return ("binary", "sub", left, right)
        exact_operators = {
            ast.FloorDiv: "div",
            ast.Mod: "mod",
            ast.LShift: "lshift",
            ast.RShift: "rshift",
        }
        for operator_type, name in exact_operators.items():
            if isinstance(node.op, operator_type):
                return ("binary", name, left, right)
        raise _UnsupportedCanonicalNode(type(node.op).__name__)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            operator = "and"
        elif isinstance(node.op, ast.Or):
            operator = "or"
        else:
            raise _UnsupportedCanonicalNode(type(node.op).__name__)
        values = []
        for child in node.values:
            key = _canonical_ast_key(child)
            if isinstance(key, tuple) and len(key) == 3 and key[:2] == ("bool", operator):
                candidates = key[2]
            else:
                candidates = (key,)
            for candidate in candidates:
                if candidate not in values:
                    values.append(candidate)
        if len(values) == 1:
            return values[0]
        return ("bool", operator, tuple(values))
    if isinstance(node, ast.Compare):
        current = _canonical_ast_key(node.left)
        comparisons = []
        for operator, comparator in zip(node.ops, node.comparators):
            right = _canonical_ast_key(comparator)
            comparisons.append(_canonical_compare(operator, current, right))
            current = right
        if len(comparisons) == 1:
            return comparisons[0]
        return ("bool", "and", tuple(comparisons))
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "bool"
            and len(node.args) == 1
            and not node.keywords
        ):
            return _canonical_ast_key(node.args[0])
        raise _UnsupportedCanonicalNode("Call")
    raise _UnsupportedCanonicalNode(type(node).__name__)


def invariant_dedup_key(inv: str) -> Hashable:
    """Return a conservative semantic key for a scalar-integer invariant.

    This deliberately avoids algebraic normalization, Boolean reordering,
    cancellation, and division/modulo rewrites.  It is a fast deduplication
    helper, not a theorem prover; no SMT solver is imported or invoked.
    """
    normalized = normalize_invariant(inv)
    if not normalized:
        return ("raw", "")
    try:
        tree = ast.parse(_acsl_to_py(normalized).strip(), mode="eval")
        return ("ast", _canonical_ast_key(tree))
    except (SyntaxError, TypeError, ValueError, _UnsupportedCanonicalNode):
        return ("raw", normalized)


# C integer division/modulo: truncation toward zero (Python's // and % floor).
def _c_div(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _c_mod(a, b):
    return a - _c_div(a, b) * b


class _CDivTransformer(ast.NodeTransformer):
    """Rewrite every Div/FloorDiv/Mod into __cdiv__/__cmod__ calls so the
    evaluator matches the C semantics the sampled states were produced under."""

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, (ast.Div, ast.FloorDiv)):
            fn = "__cdiv__"
        elif isinstance(node.op, ast.Mod):
            fn = "__cmod__"
        else:
            return node
        return ast.copy_location(
            ast.Call(func=ast.Name(id=fn, ctx=ast.Load()),
                     args=[node.left, node.right], keywords=[]),
            node,
        )


class _VectorTransformer(ast.NodeTransformer):
    """Make Python boolean AST nodes work element-wise on NumPy arrays."""

    @staticmethod
    def _fold(name: str, values):
        result = values[0]
        for value in values[1:]:
            result = ast.Call(
                func=ast.Name(id=name, ctx=ast.Load()),
                args=[result, value],
                keywords=[],
            )
        return result

    def visit_BoolOp(self, node):
        values = [self.visit(value) for value in node.values]
        name = "__logical_and__" if isinstance(node.op, ast.And) else "__logical_or__"
        return ast.copy_location(self._fold(name, values), node)

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="__logical_not__", ctx=ast.Load()),
                    args=[operand],
                    keywords=[],
                ),
                node,
            )
        node.operand = operand
        return node

    def visit_Compare(self, node):
        left = self.visit(node.left)
        comparators = [self.visit(value) for value in node.comparators]
        comparisons = []
        current = left
        for operator, right in zip(node.ops, comparators):
            comparisons.append(ast.Compare(left=current, ops=[operator], comparators=[right]))
            current = right
        result = comparisons[0]
        if len(comparisons) > 1:
            result = self._fold("__logical_and__", comparisons)
        return ast.copy_location(result, node)

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "bool" and len(node.args) == 1:
            return ast.copy_location(node.args[0], node)
        return node


# Cache compiled expressions across calls (predicates evaluated on many states).
_COMPILE_CACHE: Dict[str, object] = {}
_VECTOR_COMPILE_CACHE: Dict[str, object] = {}
_SAFE_GLOBALS = {"__builtins__": {}}


def _compile(expr: str):
    # strip: `!` -> ` not ` substitution can leave leading whitespace, which
    # ast.parse(mode="eval") rejects as an IndentationError — silently turning
    # every `!(...)`-shaped invariant into dead weight (never evaluated)
    py = _acsl_to_py(expr).strip()
    code = _COMPILE_CACHE.get(py)
    if code is None:
        tree = _CDivTransformer().visit(ast.parse(py, mode="eval"))
        ast.fix_missing_locations(tree)
        code = compile(tree, "<acsl>", "eval")
        _COMPILE_CACHE[py] = code
    return code


def _compile_vector(expr: str):
    py = _acsl_to_py(expr).strip()
    code = _VECTOR_COMPILE_CACHE.get(py)
    if code is None:
        tree = ast.parse(py, mode="eval")
        tree = _CDivTransformer().visit(tree)
        tree = _VectorTransformer().visit(tree)
        ast.fix_missing_locations(tree)
        code = compile(tree, "<acsl-vector>", "eval")
        _VECTOR_COMPILE_CACHE[py] = code
    return code


def eval_predicate(expr: str, state: "State") -> Optional[bool]:
    """
    Evaluate an ACSL boolean predicate at `state`.

    Returns True / False, or None if it cannot be grounded/evaluated
    (unknown identifiers left over, or an evaluation error).
    """
    if expr is None:
        return None
    expr = expr.strip()
    if not expr:
        return None
    try:
        code = _compile(expr)
    except (SyntaxError, TypeError, ValueError):
        return None
    ns: Dict[str, int] = {}
    for k, v in state.vars.items():
        ns[k] = int(v)
    for k, v in state.pre.items():
        ns[f"{k}__PRE__"] = int(v)
    for k, v in state.loop_entry.items():
        ns[f"{k}__LOOP_ENTRY__"] = int(v)
    # any var not bound but referenced -> unknown
    try:
        names = code.co_names
    except AttributeError:
        names = ()
    allowed = set(ns.keys()) | {
        "True", "False", "None", "bool", "abs",
        "__cdiv__", "__cmod__",
    }
    for nm in names:
        if nm not in allowed:
            return None
    ns["abs"] = abs
    ns["bool"] = bool
    ns["__cdiv__"] = _c_div
    ns["__cmod__"] = _c_mod
    try:
        result = eval(code, _SAFE_GLOBALS, ns)  # noqa: S307 - locked-down namespace
    except Exception:
        return None
    if isinstance(result, bool):
        return result
    try:
        return bool(result)
    except Exception:
        return None


def first_falsifying_state(expr: str, states: List[State]) -> Optional[State]:
    """Return the first sampled state falsifying ``expr``.

    NumPy evaluates the same predicate over every state column at once.  The
    scalar fallback stops on an unevaluable expression, leaving syntax and
    induction decisions to Frama-C rather than spending time rescanning a
    predicate the lite evaluator cannot ground.
    """
    if not states:
        return None
    try:
        import numpy as np

        code = _compile_vector(expr)
        function_names = {
            "abs", "__cdiv__", "__cmod__", "__logical_and__",
            "__logical_or__", "__logical_not__",
        }
        columns = {}
        for name in code.co_names:
            if name in function_names:
                continue
            if name.endswith("__LOOP_ENTRY__"):
                key = name[:-14]
                if any(key not in state.loop_entry for state in states):
                    return None
                columns[name] = np.fromiter(
                    (state.loop_entry[key] for state in states),
                    dtype=object,
                    count=len(states),
                )
            elif name.endswith("__PRE__"):
                key = name[:-7]
                if any(key not in state.pre for state in states):
                    return None
                columns[name] = np.fromiter(
                    (state.pre[key] for state in states), dtype=object, count=len(states)
                )
            else:
                if any(name not in state.vars for state in states):
                    return None
                columns[name] = np.fromiter(
                    (state.vars[name] for state in states), dtype=object, count=len(states)
                )

        def cdiv(left, right):
            left, right = np.asarray(left), np.asarray(right)
            if np.any(right == 0):
                raise ZeroDivisionError
            quotient = np.floor_divide(np.abs(left), np.abs(right))
            return np.where((left >= 0) == (right >= 0), quotient, -quotient)

        def cmod(left, right):
            return np.asarray(left) - cdiv(left, right) * np.asarray(right)

        namespace = {
            **columns,
            "abs": np.abs,
            "__cdiv__": cdiv,
            "__cmod__": cmod,
            "__logical_and__": np.logical_and,
            "__logical_or__": np.logical_or,
            "__logical_not__": np.logical_not,
        }
        result = np.asarray(eval(code, _SAFE_GLOBALS, namespace), dtype=bool)
        if result.ndim == 0:
            return None if bool(result) else states[0]
        false_indices = np.flatnonzero(~result)
        return states[int(false_indices[0])] if false_indices.size else None
    except Exception:
        for state in states:
            result = eval_predicate(expr, state)
            if result is False:
                return state
            if result is None:
                return None
        return None
