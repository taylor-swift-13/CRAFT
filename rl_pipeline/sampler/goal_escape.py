"""Target-directed hard escape traces for negative-coverage reward.

The ordinary sampler remains target-hidden and produces two semantic families:
relation and escape.  At reward time the target is available to the scorer, so
this module adds a small, rollout-adaptive *escape* stratum.  Every generated
trace reaches an assertion valuation that violates the target while preserving
the loop guard/path context.  Candidate-adaptive SMT models additionally keep
one rollout's surviving invariants true, making them direct witnesses of what
that invariant set still permits.

Only concrete states accepted by the deployed predicate evaluator enter the
ledger.  If Z3 is unavailable or cannot model an expression, local target
mutations and observed error traces remain available; no symbolic placeholder
is ever scored as a negative trace.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import re
import time
from typing import Optional

try:  # The reward image installs z3-solver; keep local-only mining as fallback.
    import z3
except ImportError:  # pragma: no cover - exercised only by minimal deployments
    z3 = None

from ..common.program import (
    bind_integer_constants,
    iter_acsl_clauses,
    parse_program,
    state_external_integer_constants,
)
from ..common.state import (
    State,
    _acsl_to_py,
    _c_name,
    eval_predicate,
    normalize_invariant,
)
from .example_sampler import ExampleSampler, ExampleSet, _NegativeCandidate


GOAL_ESCAPE_BUDGET = 12
_RAW_ESCAPE_CAP = 320
_TARGET_DELTAS = (1, -1, 2, -2, 3, -3, 5, -5, 8, -8, 13, -13, 21, -21)
_SMT_TIMEOUT_MS = 40
_SMT_BASES_PER_TARGET = 24
_MINING_TIME_BUDGET_SECONDS = 2.0


@dataclass(frozen=True)
class GoalEscapeResult:
    groups: list[list[State]]
    target_count: int
    parsed_target_count: int
    raw_groups: int
    observed_error_groups: int
    solver_available: bool
    seconds: float

    @property
    def parseable(self) -> bool:
        return self.target_count > 0 and self.parsed_target_count == self.target_count


@dataclass(frozen=True)
class _TargetPoint:
    expression: str
    location: str
    path_conditions: tuple[str, ...]
    observed_trace_safe: bool


def _match_delimiter(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _statement_end(mask: str, start: int) -> int:
    """Return the exclusive end of one braced/simple/if C statement."""
    while start < len(mask) and mask[start].isspace():
        start += 1
    if start >= len(mask):
        return start
    if mask[start] == "{":
        close = _match_delimiter(mask, start, "{", "}")
        return len(mask) if close < 0 else close + 1
    if re.match(r"if\b", mask[start:]):
        open_paren = mask.find("(", start)
        close_paren = _match_delimiter(mask, open_paren, "(", ")")
        end = _statement_end(mask, close_paren + 1)
        match_else = re.match(r"\s*else\b", mask[end:])
        if match_else:
            end = _statement_end(mask, end + match_else.end())
        return end
    semicolon = mask.find(";", start)
    return len(mask) if semicolon < 0 else semicolon + 1


def _masked_source(source: str) -> str:
    return re.sub(
        r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        lambda match: re.sub(r"[^\n]", " ", match.group(0)),
        source,
        flags=re.DOTALL,
    )


def _path_conditions(source: str, point: int) -> tuple[str, ...]:
    """Lexical enclosing-if path for a target, including else negations."""
    mask = _masked_source(source)
    conditions: list[tuple[int, str]] = []
    for match in re.finditer(r"\bif\s*\(", mask):
        open_paren = mask.find("(", match.start(), match.end())
        close_paren = _match_delimiter(mask, open_paren, "(", ")")
        if close_paren < 0:
            continue
        condition = source[open_paren + 1:close_paren].strip()
        then_start = close_paren + 1
        while then_start < len(mask) and mask[then_start].isspace():
            then_start += 1
        then_end = _statement_end(mask, then_start)
        if then_start <= point < then_end:
            conditions.append((match.start(), condition))
            continue
        match_else = re.match(r"\s*else\b", mask[then_end:])
        if match_else:
            else_start = then_end + match_else.end()
            while else_start < len(mask) and mask[else_start].isspace():
                else_start += 1
            else_end = _statement_end(mask, else_start)
            if else_start <= point < else_end:
                conditions.append((match.start(), f"!({condition})"))
    return tuple(condition for _, condition in sorted(conditions))


def _substitute_current(expression: str, name: str, replacement: str) -> str:
    protected: list[str] = []

    def hold(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"__CRAFT_AT_{len(protected) - 1}__"

    output = re.sub(r"\\at\([^)]*\)", hold, expression)
    output = re.sub(rf"\b{re.escape(name)}\b", f"({replacement})", output)
    for index, value in enumerate(protected):
        output = output.replace(f"__CRAFT_AT_{index}__", value)
    return output


def _backward_straightline(
    source: str,
    start: int,
    stop: int,
    expressions: tuple[str, ...],
) -> tuple[str, ...]:
    """Move a target back to the loop head through a simple code prefix."""
    region = source[start:stop]
    mask = re.sub(
        r"/\*.*?\*/|//[^\n]*",
        lambda match: re.sub(r"[^\n]", " ", match.group(0)),
        region,
        flags=re.DOTALL,
    )
    branch = re.search(r"\b(?:if|switch|while|for)\s*\(", mask)
    if branch:
        region = region[:branch.start()]
        mask = mask[:branch.start()]

    assignments: list[tuple[int, str, str]] = []
    pattern = re.compile(
        r"(?:\b(?:int|long|short|char|_Bool|unsigned|signed)\s+)?"
        r"\b([A-Za-z_]\w*)\s*(=|\+=|-=|\*=)\s*([^;]+);"
        r"|\b([A-Za-z_]\w*)\s*(\+\+|--)\s*;"
    )
    for match in pattern.finditer(mask):
        if match.group(1):
            name, operator = match.group(1), match.group(2)
            rhs = region[match.start(3):match.end(3)].strip()
            if operator == "+=":
                rhs = f"({name}) + ({rhs})"
            elif operator == "-=":
                rhs = f"({name}) - ({rhs})"
            elif operator == "*=":
                rhs = f"({name}) * ({rhs})"
        else:
            name, operator = match.group(4), match.group(5)
            rhs = f"({name}) {'+' if operator == '++' else '-'} 1"
        assignments.append((match.start(), name, rhs))

    rewritten = list(expressions)
    for _position, name, rhs in reversed(assignments):
        rewritten = [
            _substitute_current(expression, name, rhs)
            for expression in rewritten
        ]
    return tuple(rewritten)


def _target_points(source: str) -> list[_TargetPoint]:
    program = parse_program(source)
    loop = program.loop
    points: list[_TargetPoint] = []
    for start, _stop, expression in iter_acsl_clauses(source, "assert"):
        if loop.body_open < start < loop.body_close:
            location = "inside"
            prefix_start = loop.body_open + 1
        elif start > loop.body_close:
            location = "after"
            prefix_start = loop.body_close + 1
        else:
            continue
        path_conditions = _path_conditions(source, start)
        prefix_region = source[prefix_start:start]
        prefix_mask = re.sub(
            r"/\*.*?\*/|//[^\n]*",
            lambda match: re.sub(r"[^\n]", " ", match.group(0)),
            prefix_region,
            flags=re.DOTALL,
        )
        rewritten = _backward_straightline(
            source,
            prefix_start,
            start,
            (normalize_invariant(expression), *path_conditions),
        )
        points.append(_TargetPoint(
            expression=rewritten[0],
            location=location,
            path_conditions=tuple(rewritten[1:]),
            observed_trace_safe=not bool(re.search(
                r"\b(?:if|switch|while|for)\s*\(", prefix_mask
            )),
        ))
    return points


class _UnsupportedSMT(ValueError):
    pass


def _z3_bool(value):
    return value if z3.is_bool(value) else value != 0


def _z3_int(value):
    return z3.If(value, 1, 0) if z3.is_bool(value) else value


def _z3_expression(expression: str, variables: dict, base: State):
    tree = ast.parse(_acsl_to_py(expression), mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return z3.BoolVal(node.value)
            if isinstance(node.value, int):
                return z3.IntVal(node.value)
            raise _UnsupportedSMT(type(node.value).__name__)
        if isinstance(node, ast.Name):
            name = _c_name(node.id)
            if name.endswith("__PRE__"):
                source_name = name[:-7]
                if source_name not in base.pre:
                    raise _UnsupportedSMT(name)
                return z3.IntVal(base.pre[source_name])
            if name.endswith("__LOOP_ENTRY__"):
                source_name = name[:-14]
                if source_name not in base.loop_entry:
                    raise _UnsupportedSMT(name)
                return z3.IntVal(base.loop_entry[source_name])
            if name not in variables:
                raise _UnsupportedSMT(name)
            return variables[name]
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.Not):
                return z3.Not(_z3_bool(value))
            if isinstance(node.op, ast.USub):
                return -_z3_int(value)
            if isinstance(node.op, ast.UAdd):
                return _z3_int(value)
            raise _UnsupportedSMT(type(node.op).__name__)
        if isinstance(node, ast.BoolOp):
            values = [_z3_bool(visit(value)) for value in node.values]
            if isinstance(node.op, ast.And):
                return z3.And(*values)
            if isinstance(node.op, ast.Or):
                return z3.Or(*values)
            raise _UnsupportedSMT(type(node.op).__name__)
        if isinstance(node, ast.BinOp):
            left, right = _z3_int(visit(node.left)), _z3_int(visit(node.right))
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.FloorDiv):
                return left / right
            raise _UnsupportedSMT(type(node.op).__name__)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            clauses = []
            for operator, comparator in zip(node.ops, node.comparators):
                right = visit(comparator)
                if isinstance(operator, ast.Eq):
                    clause = left == right
                elif isinstance(operator, ast.NotEq):
                    clause = left != right
                elif isinstance(operator, ast.Lt):
                    clause = _z3_int(left) < _z3_int(right)
                elif isinstance(operator, ast.LtE):
                    clause = _z3_int(left) <= _z3_int(right)
                elif isinstance(operator, ast.Gt):
                    clause = _z3_int(left) > _z3_int(right)
                elif isinstance(operator, ast.GtE):
                    clause = _z3_int(left) >= _z3_int(right)
                else:
                    raise _UnsupportedSMT(type(operator).__name__)
                clauses.append(clause)
                left = right
            return z3.And(*clauses)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "bool" and len(node.args) == 1:
                return _z3_bool(visit(node.args[0]))
            if node.func.id == "abs" and len(node.args) == 1:
                value = _z3_int(visit(node.args[0]))
                return z3.If(value >= 0, value, -value)
        raise _UnsupportedSMT(type(node).__name__)

    return visit(tree)


def _solve_goal_state(
    base: State,
    target: str,
    guard: str,
    path_conditions: tuple[str, ...],
    invariants: list[str],
    desired_guard: bool,
    fixed_variables: set[str],
    unsigned_variables: set[str],
) -> Optional[State]:
    if z3 is None:
        return None
    variables = {
        name: z3.Int(f"v_{index}_{name}")
        for index, name in enumerate(sorted(base.vars))
    }
    try:
        target_formula = _z3_bool(_z3_expression(target, variables, base))
        guard_formula = _z3_bool(_z3_expression(guard, variables, base))
        path_formulas = [
            _z3_bool(_z3_expression(condition, variables, base))
            for condition in path_conditions
        ]
        invariant_formulas = [
            _z3_bool(_z3_expression(invariant, variables, base))
            for invariant in invariants
        ]
    except (SyntaxError, _UnsupportedSMT, z3.Z3Exception):
        return None

    solver = z3.Solver()
    solver.set(timeout=_SMT_TIMEOUT_MS)
    solver.add(z3.Not(target_formula))
    solver.add(guard_formula if desired_guard else z3.Not(guard_formula))
    solver.add(*path_formulas, *invariant_formulas)
    changed = []
    for name, variable in variables.items():
        value = base.vars[name]
        solver.add(variable >= -(2**31), variable <= 2**31 - 1)
        if name in unsigned_variables:
            solver.add(variable >= 0)
        if name in fixed_variables:
            solver.add(variable == value)
        changed.append(variable != value)
    if not changed:
        return None
    solver.add(z3.Or(*changed))
    if solver.check() != z3.sat:
        return None
    model = solver.model()
    values = {
        name: model.eval(variable, model_completion=True).as_long()
        for name, variable in variables.items()
    }
    state = base.with_vars(values)
    # Concrete admission is authoritative; it catches C/Z3 division corners.
    if eval_predicate(target, state) is not False:
        return None
    if eval_predicate(guard, state) is not desired_guard:
        return None
    if not all(eval_predicate(condition, state) is True for condition in path_conditions):
        return None
    if not all(eval_predicate(invariant, state) is True for invariant in invariants):
        return None
    return state


def _solver_bases(states: list[State], limit: int) -> list[State]:
    """Stratify concrete Pre/LoopEntry contexts, including every boundary."""
    by_context: dict[tuple, list[State]] = {}
    for state in states:
        by_context.setdefault(state.context_key(), []).append(state)
    groups = list(by_context.values())
    selected: list[State] = []
    representatives = [group[0] for group in groups]

    def keep(state: State) -> bool:
        if state not in selected:
            selected.append(state)
        return len(selected) == limit

    names = sorted({name for state in representatives for name in state.vars})
    for name in names:
        present = [state for state in representatives if name in state.vars]
        if present:
            if keep(min(present, key=lambda state: state.vars[name])):
                return selected
            if keep(max(present, key=lambda state: state.vars[name])):
                return selected
    for position in (0, -1):
        for group in groups:
            if keep(group[position]):
                return selected
    depth = 1
    while len(selected) < limit:
        added = False
        for group in groups:
            if depth < len(group) - 1:
                state = group[depth]
                if state not in selected:
                    selected.append(state)
                    added = True
                    if len(selected) == limit:
                        return selected
        if not added:
            break
        depth += 1
    return selected


def _select_hard_groups(
    raw_groups: list[list[State]],
    candidate_survivors: list[list[str]],
    constants: dict[str, int],
    budget: int,
) -> list[list[State]]:
    """Retain diverse traces that expose the most still-perfect rollouts."""
    conditions = [
        [bind_integer_constants(invariant, constants) for invariant in invariants]
        for invariants in candidate_survivors
    ]
    killed_by: list[set[int]] = []
    for group in raw_groups:
        killed = set()
        for candidate_index, invariant_conditions in enumerate(conditions):
            if any(
                eval_predicate(condition, state) is False
                for condition in invariant_conditions
                for state in group
            ):
                killed.add(candidate_index)
        killed_by.append(killed)

    remaining = set(range(len(raw_groups)))
    still_full = set(range(len(candidate_survivors)))
    selected_indices: list[int] = []
    signatures: set[tuple[int, ...]] = set()
    while remaining and len(selected_indices) < budget:
        def rank(index: int) -> tuple:
            killed = killed_by[index]
            newly_exposed = len(still_full - killed)
            signature = tuple(sorted(killed))
            split = min(len(killed), len(candidate_survivors) - len(killed))
            hardness = len(candidate_survivors) - len(killed)
            return newly_exposed, int(signature not in signatures), split, hardness, -index

        best = max(remaining, key=rank)
        selected_indices.append(best)
        remaining.remove(best)
        signatures.add(tuple(sorted(killed_by[best])))
        # A rollout stays perfect only if it rejects every selected trace.
        still_full &= killed_by[best]
    return [raw_groups[index] for index in selected_indices]


def mine_goal_escape_groups(
    full_source: str,
    examples: ExampleSet,
    candidate_survivors: list[list[str]],
    loop_idx: int = 0,
    budget: int = GOAL_ESCAPE_BUDGET,
) -> GoalEscapeResult:
    """Mine concrete assertion-failing escape traces for one rollout group."""
    started = time.perf_counter()
    empty = lambda target_count=0, parsed=0: GoalEscapeResult(
        groups=[],
        target_count=target_count,
        parsed_target_count=parsed,
        raw_groups=0,
        observed_error_groups=0,
        solver_available=z3 is not None,
        seconds=time.perf_counter() - started,
    )
    if budget <= 0 or not candidate_survivors:
        return empty()
    try:
        determinized_source = ExampleSampler._determinize_source(full_source)
        sampling = parse_program(determinized_source)
        if loop_idx != 0:
            # The concrete sampler currently supports one loop; fail closed for
            # future multi-loop callers rather than attaching a target wrongly.
            return empty()
        points = _target_points(determinized_source)
    except (ValueError, IndexError):
        return empty()
    if not points:
        return empty()

    constants = state_external_integer_constants(sampling)
    guard_condition = bind_integer_constants(
        sampling.loop.guard or "", constants
    )
    positives = examples.pos(loop_idx)
    if not positives:
        return empty(len(points), 0)
    terminals = [
        state for state in positives
        if eval_predicate(guard_condition, state) is False
    ]
    reachable = {state.key() for state in positives}
    entry_feasible = ExampleSampler(determinized_source)._entry_feasible_fn(sampling)
    modified_variables = set(ExampleSampler._modified_vars(sampling, loop_idx))
    fixed_variables = set(sampling.pre_vars) - modified_variables
    unsigned_variables = set(sampling.unsigned_vars)
    synthetic: list[_NegativeCandidate] = []
    observed_groups: list[list[State]] = []
    seen: set[tuple] = set()
    observed_seen: set[tuple[int, tuple]] = set()
    parsed_points = 0
    deadline = started + _MINING_TIME_BUDGET_SECONDS

    for point_index, point in enumerate(points):
        target_condition = bind_integer_constants(point.expression, constants)
        path_conditions = tuple(
            bind_integer_constants(condition, constants)
            for condition in point.path_conditions
        )
        desired_guard = point.location == "inside"
        target_variables = [
            variable for variable in sampling.pre_vars
            if re.search(rf"\b{re.escape(variable)}\b", point.expression)
        ]
        local_pool = positives if desired_guard else terminals
        local_bases = [
            state for state in ExampleSampler._bases(local_pool)
            if eval_predicate(guard_condition, state) is desired_guard
            and all(eval_predicate(condition, state) is True for condition in path_conditions)
            and eval_predicate(target_condition, state) is True
        ]
        solve_bases = [
            state for state in _solver_bases(positives, _SMT_BASES_PER_TARGET)
            if all(eval_predicate(condition, state) is not None for condition in path_conditions)
        ]
        if any(
            eval_predicate(target_condition, state) is not None
            for state in ExampleSampler._bases(positives)
        ):
            parsed_points += 1

        # A sampled execution that reaches the assertion error is a negative
        # *goal trace*, although its states correctly remain in the positive set.
        if point.observed_trace_safe:
            observed_pool = positives if desired_guard else terminals
            for state in _solver_bases(observed_pool, 64):
                key = (point_index, state.key())
                if key in observed_seen:
                    continue
                if eval_predicate(guard_condition, state) is not desired_guard:
                    continue
                if not all(eval_predicate(condition, state) is True for condition in path_conditions):
                    continue
                if eval_predicate(target_condition, state) is not False:
                    continue
                observed_seen.add(key)
                observed_groups.append([state])

        def admit(
            base: State,
            values: dict[str, int],
            axes: tuple[str, ...],
            directions: tuple[int, ...],
        ) -> None:
            state = base.with_vars(values)
            key = state.key()
            if key in reachable or key in seen or entry_feasible(state):
                return
            if eval_predicate(guard_condition, state) is not desired_guard:
                return
            if not all(eval_predicate(condition, state) is True for condition in path_conditions):
                return
            if eval_predicate(target_condition, state) is not False:
                return
            seen.add(key)
            synthetic.append(_NegativeCandidate(
                state=state,
                bucket=("local", point_index, point.location, axes, directions),
                distance=sum(abs(values[axis] - base.vars[axis]) for axis in axes),
            ))

        for base in (local_bases if target_variables else []):
            present = [variable for variable in target_variables if variable in base.vars]
            for variable in present:
                for delta in _TARGET_DELTAS:
                    values = dict(base.vars)
                    values[variable] += delta
                    admit(base, values, (variable,), (1 if delta > 0 else -1,))
            for first in range(len(present)):
                for second in range(first + 1, len(present)):
                    left, right = present[first], present[second]
                    for delta in (1, -1, 2, -2, 5, -5):
                        for left_delta, right_delta in ((delta, delta), (delta, -delta)):
                            values = dict(base.vars)
                            values[left] += left_delta
                            values[right] += right_delta
                            admit(
                                base,
                                values,
                                (left, right),
                                (
                                    1 if left_delta > 0 else -1,
                                    1 if right_delta > 0 else -1,
                                ),
                            )

        # Keep each rollout true while falsifying the target: this is the hard
        # escape that a target-insufficient invariant set fails to reject.
        for candidate_index, survivors in enumerate(candidate_survivors):
            if not survivors or time.perf_counter() >= deadline:
                continue
            invariants = [
                bind_integer_constants(invariant, constants)
                for invariant in survivors
            ]
            for base in solve_bases:
                if time.perf_counter() >= deadline:
                    break
                solved = _solve_goal_state(
                    base,
                    target_condition,
                    guard_condition,
                    path_conditions,
                    invariants,
                    desired_guard,
                    fixed_variables,
                    unsigned_variables,
                )
                if solved is None:
                    continue
                key = solved.key()
                if key in reachable or key in seen or entry_feasible(solved):
                    continue
                changed = tuple(
                    name for name in solved.vars
                    if solved.vars[name] != base.vars[name]
                )
                if not changed:
                    continue
                seen.add(key)
                synthetic.append(_NegativeCandidate(
                    state=solved,
                    bucket=("smt", point_index, candidate_index),
                    distance=sum(
                        abs(solved.vars[name] - base.vars[name])
                        for name in changed
                    ),
                ))
                break

    solved_candidates = [
        candidate for candidate in synthetic
        if candidate.bucket and candidate.bucket[0] == "smt"
    ]
    local_candidates = [
        candidate for candidate in synthetic
        if not candidate.bucket or candidate.bucket[0] != "smt"
    ]
    selected = ExampleSampler._round_robin(
        solved_candidates, min(64, _RAW_ESCAPE_CAP)
    )
    selected.extend(ExampleSampler._round_robin(
        local_candidates, _RAW_ESCAPE_CAP - len(selected)
    ))
    raw_groups = observed_groups[:64] + [[candidate.state] for candidate in selected]
    raw_groups = raw_groups[:_RAW_ESCAPE_CAP]
    groups = _select_hard_groups(
        raw_groups,
        candidate_survivors,
        constants,
        min(budget, GOAL_ESCAPE_BUDGET),
    )
    return GoalEscapeResult(
        groups=groups,
        target_count=len(points),
        parsed_target_count=parsed_points,
        raw_groups=len(raw_groups),
        observed_error_groups=len(observed_groups),
        solver_available=z3 is not None,
        seconds=time.perf_counter() - started,
    )

