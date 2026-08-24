"""Prototype target-directed escape traces on the frozen Full-832 candidates.

Relation traces are loaded unchanged from a fixed sampler manifest. Escape
traces are retained/generated only when they contain an exit valuation that
falsifies the benchmark target. The target is used solely by this experimental
escape constructor; candidate invariants and Houdini survivors remain frozen.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
import z3

from experiments.current_sampler_rescore_832 import load_candidates
from experiments.gpt5nano_full832.common import discover_tasks
from experiments.gpt5nano_full832.samples import (
    load_sample,
    load_sample_manifest,
)
from experiments.negative_sampler_hard3_eval_832 import metrics
from rl_pipeline.common.program import (
    bind_integer_constants,
    iter_acsl_clauses,
    parse_program,
    state_external_integer_constants,
)
from rl_pipeline.common.state import (
    State,
    _acsl_to_py,
    _c_name,
    eval_predicate,
    normalize_invariant,
)
from rl_pipeline.sampler import ExampleSampler
from rl_pipeline.sampler.example_sampler import _NegativeCandidate


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "results" / "negative_sampler_relation_escape_832"
ESCAPE_BUDGET = 12
RAW_ESCAPE_CAP = 320
TARGET_DELTAS = (1, -1, 2, -2, 3, -3, 5, -5, 8, -8, 13, -13, 21, -21)
_SMT_TIMEOUT_MS = 40
_SMT_BASES_PER_TARGET = 24
_RELATION_WEIGHT = 0.55
_DYNAMIC_ESCAPE_WEIGHT = 0.15
_GOAL_ESCAPE_WEIGHT = 0.30
_GOAL_HARDNESS_POWER = 5


@dataclass(frozen=True)
class GoalEscapeAudit:
    groups: list[list[State]]
    target_post: str
    target_variables: list[str]
    terminal_bases: int
    continued_groups: int
    synthetic_groups: int
    parseable: bool


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
    """End offset (exclusive) of one braced/simple/if C statement."""
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


def _path_conditions(source: str, point: int) -> tuple[str, ...]:
    """Lexical enclosing-if path for a target, including else negations."""
    mask = re.sub(
        r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        lambda match: re.sub(r"[^\n]", " ", match.group(0)),
        source,
        flags=re.DOTALL,
    )
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
    output = re.sub(
        rf"\b{re.escape(name)}\b", f"({replacement})", output
    )
    for index, value in enumerate(protected):
        output = output.replace(f"__CRAFT_AT_{index}__", value)
    return output


def _backward_straightline(
    source: str,
    start: int,
    stop: int,
    expressions: tuple[str, ...],
) -> tuple[str, ...]:
    """Substitute the unconditional straight-line prefix before a target."""
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
    points = []
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


def _z3_expression(
    expression: str,
    variables: dict[str, z3.ArithRef],
    base: State,
):
    tree = ast.parse(_acsl_to_py(expression), mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (bool, int)):
                return z3.BoolVal(node.value) if isinstance(node.value, bool) else z3.IntVal(node.value)
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
                # Z3 and C differ for negative operands. The concrete
                # evaluator below is the final admission gate, so models with
                # a semantic mismatch are discarded.
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
) -> State | None:
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
    solver.add(z3.Or(*changed))
    if solver.check() != z3.sat:
        return None
    model = solver.model()
    values = {
        name: model.eval(variable, model_completion=True).as_long()
        for name, variable in variables.items()
    }
    state = base.with_vars(values)
    # Re-check with the deployed predicate evaluator; this catches unsupported
    # C/Z3 corner semantics before a model can enter the negative ledger.
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
    """Round-robin contexts so a long first trace cannot hide boundary inputs."""
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

    # Requirement boundaries and large constants often occur in a late input
    # context. Put every variable's min/max context ahead of ordinary order.
    names = sorted({name for state in representatives for name in state.vars})
    for name in names:
        present = [state for state in representatives if name in state.vars]
        if present:
            if keep(min(present, key=lambda state: state.vars[name])):
                return selected
            if keep(max(present, key=lambda state: state.vars[name])):
                return selected

    positions = [0, -1]
    for position in positions:
        for group in groups:
            state = group[position]
            if keep(state):
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


def _goal_escape_groups(task, examples, candidates: list[dict]) -> GoalEscapeAudit:
    source = task.source_path.read_text(errors="ignore")
    determinized_source = ExampleSampler._determinize_source(source)
    sampling = parse_program(determinized_source)
    points = _target_points(determinized_source)
    target = " && ".join(f"({point.expression})" for point in points)
    if not points:
        return GoalEscapeAudit([], target, [], 0, 0, 0, False)

    constants = state_external_integer_constants(sampling)
    guard_condition = bind_integer_constants(
        sampling.loop.guard or "", state_external_integer_constants(sampling)
    )
    positives = examples.pos(0)
    terminals = [
        state for state in positives
        if eval_predicate(guard_condition, state) is False
    ]
    reachable = {state.key() for state in positives}
    entry_feasible = ExampleSampler(determinized_source)._entry_feasible_fn(
        sampling
    )
    synthetic: list[_NegativeCandidate] = []
    seen: set[tuple] = set()
    observed_goal_failures: list[list[State]] = []
    observed_seen: set[tuple[int, tuple]] = set()
    modified_variables = set(ExampleSampler._modified_vars(sampling))
    fixed_variables = set(sampling.pre_vars) - modified_variables

    parsed_points = 0
    all_target_variables: set[str] = set()
    for point_index, point in enumerate(points):
        target_condition = bind_integer_constants(point.expression, constants)
        path_conditions = tuple(
            bind_integer_constants(condition, constants)
            for condition in point.path_conditions
        )
        target_variables = [
            variable for variable in sampling.pre_vars
            if re.search(rf"\b{re.escape(variable)}\b", point.expression)
        ]
        all_target_variables.update(target_variables)
        desired_guard = point.location == "inside"
        local_pool = positives if desired_guard else terminals
        local_bases = [
            state for state in ExampleSampler._bases(local_pool)
            if eval_predicate(guard_condition, state) is desired_guard
            and all(
                eval_predicate(condition, state) is True
                for condition in path_conditions
            )
            and eval_predicate(target_condition, state) is True
        ]
        # Solver bases provide only a concrete Pre/LoopEntry context; the SMT
        # guard constraint constructs the assertion-point head. Include all
        # contexts so a capped large-bound run is not hidden merely because
        # smaller inputs produced ordinary terminals.
        solve_pool = positives
        solve_bases = [
            state for state in _solver_bases(
                solve_pool, _SMT_BASES_PER_TARGET
            )
            if all(
                eval_predicate(condition, state) is not None
                for condition in path_conditions
            )
        ]
        if any(
            eval_predicate(target_condition, state) is not None
            for state in ExampleSampler._bases(positives)
        ):
            parsed_points += 1

        # A reachable assertion-violating execution is itself a negative goal
        # trace (the program is unsafe for that sampled context). Its states
        # remain positives; retaining the trace as a goal escape gives every
        # sound invariant zero rejection credit on that obligation instead of
        # pretending the reachable valuation is off-manifold.
        if point.observed_trace_safe:
            observed_pool = positives if desired_guard else terminals
            for state in _solver_bases(observed_pool, 64):
                observed_key = (point_index, state.key())
                if observed_key in observed_seen:
                    continue
                if eval_predicate(guard_condition, state) is not desired_guard:
                    continue
                if not all(
                    eval_predicate(condition, state) is True
                    for condition in path_conditions
                ):
                    continue
                if eval_predicate(target_condition, state) is not False:
                    continue
                observed_seen.add(observed_key)
                observed_goal_failures.append([state])

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
            if not all(
                eval_predicate(condition, state) is True
                for condition in path_conditions
            ):
                return
            if eval_predicate(target_condition, state) is not False:
                return
            seen.add(key)
            synthetic.append(_NegativeCandidate(
                state=state,
                bucket=(point_index, point.location, axes, directions),
                distance=sum(
                    abs(values[axis] - base.vars[axis]) for axis in axes
                ),
            ))

        for base in (local_bases if target_variables else []):
            present = [
                variable for variable in target_variables
                if variable in base.vars
            ]
            for variable in present:
                for delta in TARGET_DELTAS:
                    values = dict(base.vars)
                    values[variable] += delta
                    admit(
                        base,
                        values,
                        (variable,),
                        (1 if delta > 0 else -1,),
                    )
            for first in range(len(present)):
                for second in range(first + 1, len(present)):
                    left, right = present[first], present[second]
                    for delta in (1, -1, 2, -2, 5, -5):
                        for left_delta, right_delta in (
                            (delta, delta), (delta, -delta)
                        ):
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

        # Candidate-adaptive hard mining: solve the abstract target obligation
        # while requiring every clause of one rollout to remain true. Such a
        # model is exactly the escape a currently full-scoring but
        # target-insufficient invariant set fails to exclude.
        for candidate_index, candidate in enumerate(candidates):
            invariants = [
                bind_integer_constants(invariant, constants)
                for invariant in candidate.get("survivors") or []
            ]
            if not invariants:
                continue
            for base in solve_bases:
                solved = _solve_goal_state(
                    base,
                    target_condition,
                    guard_condition,
                    path_conditions,
                    invariants,
                    desired_guard,
                    fixed_variables,
                    set(sampling.unsigned_vars),
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
    selected_synthetic = ExampleSampler._round_robin(
        solved_candidates, min(64, RAW_ESCAPE_CAP)
    )
    selected_synthetic.extend(ExampleSampler._round_robin(
        local_candidates, RAW_ESCAPE_CAP - len(selected_synthetic)
    ))
    # Selection against the rollout group happens in ``_score_task`` so the
    # retained target escapes are the hard, candidate-separating traces.
    groups = observed_goal_failures[:64] + [
        [candidate.state] for candidate in selected_synthetic
    ]
    groups = groups[:RAW_ESCAPE_CAP]
    return GoalEscapeAudit(
        groups=groups,
        target_post=target,
        target_variables=sorted(all_target_variables),
        terminal_bases=len(terminals),
        continued_groups=len(observed_goal_failures),
        synthetic_groups=len(selected_synthetic),
        parseable=parsed_points == len(points),
    )


def _candidate_adaptive_escapes(
    raw_groups: list[list[State]],
    candidates: list[dict],
    constants: dict[str, int],
) -> tuple[list[list[State]], list[set[int]]]:
    """Greedily retain hard goal escapes with diverse rollout signatures."""
    conditions = [
        [bind_integer_constants(invariant, constants)
         for invariant in candidate["survivors"]]
        for candidate in candidates
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
    still_full = set(range(len(candidates)))
    selected_indices: list[int] = []
    signatures: set[tuple[int, ...]] = set()
    while remaining and len(selected_indices) < ESCAPE_BUDGET:
        def rank(index: int) -> tuple:
            killed = killed_by[index]
            newly_broken = len(still_full - killed)
            signature = tuple(sorted(killed))
            novel = signature not in signatures
            # Prefer traces that break the most still-perfect candidates, then
            # signatures near a half split. Once every perfect candidate has
            # been broken, low-coverage traces remain the hardest examples.
            split = min(len(killed), len(candidates) - len(killed))
            hardness = len(candidates) - len(killed)
            return newly_broken, int(novel), split, hardness, -index

        best = max(remaining, key=rank)
        selected_indices.append(best)
        remaining.remove(best)
        signatures.add(tuple(sorted(killed_by[best])))
        still_full &= killed_by[best]

    return (
        [raw_groups[index] for index in selected_indices],
        [killed_by[index] for index in selected_indices],
    )


def _score_task(task, manifest_row: dict, candidates: list[dict]) -> dict:
    started = time.perf_counter()
    examples = load_sample(task, manifest_row)
    base_groups = [
        ([examples.neg(0)[index] for index in group], family)
        for group, family in zip(
            examples.groups(0), examples.group_families(0)
        )
    ]
    relation_groups = [group for group, family in base_groups if family == "relation"]
    existing_escape_groups = [
        group for group, family in base_groups if family == "escape"
    ]
    audit = _goal_escape_groups(task, examples, candidates)
    constants = state_external_integer_constants(parse_program(task.hidden_source))
    goal_escapes, _escape_signatures = _candidate_adaptive_escapes(
        audit.groups, candidates, constants
    )
    groups = [group for group, _family in base_groups] + goal_escapes
    rows = []
    for candidate in candidates:
        rejected = 0
        rejected_relation = 0
        rejected_existing_escape = 0
        rejected_goal_escape = 0
        for group_index, group in enumerate(groups):
            killed = False
            for invariant in candidate["survivors"]:
                condition = bind_integer_constants(invariant, constants)
                if any(
                    eval_predicate(condition, state) is False
                    for state in group
                ):
                    killed = True
                    break
            if killed:
                rejected += 1
                if group_index < len(base_groups) and (
                    base_groups[group_index][1] == "relation"
                ):
                    rejected_relation += 1
                elif group_index < len(base_groups):
                    rejected_existing_escape += 1
                else:
                    rejected_goal_escape += 1
        weighted_components = []
        if relation_groups:
            weighted_components.append((
                _RELATION_WEIGHT,
                rejected_relation / len(relation_groups),
            ))
        if existing_escape_groups:
            weighted_components.append((
                _DYNAMIC_ESCAPE_WEIGHT,
                rejected_existing_escape / len(existing_escape_groups),
            ))
        if goal_escapes:
            goal_rate = rejected_goal_escape / len(goal_escapes)
            weighted_components.append((
                _GOAL_ESCAPE_WEIGHT,
                goal_rate ** _GOAL_HARDNESS_POWER,
            ))
        stratified_score = (
            sum(weight * value for weight, value in weighted_components)
            / sum(weight for weight, _value in weighted_components)
            if weighted_components else None
        )
        rows.append({
            **candidate,
            "suite": task.suite,
            "case_id": task.case_id,
            "goal_escape_score": rejected / len(groups) if groups else None,
            "stratified_goal_score": stratified_score,
            "negative_groups": len(groups),
            "relation_groups": len(relation_groups),
            "existing_escape_groups": len(existing_escape_groups),
            "goal_escape_groups": len(goal_escapes),
            "raw_goal_escape_groups": len(audit.groups),
            "rejected_groups": rejected if groups else None,
            "rejected_relation": rejected_relation,
            "rejected_existing_escape": rejected_existing_escape,
            "rejected_goal_escape": rejected_goal_escape,
        })
    return {
        "suite": task.suite,
        "case_id": task.case_id,
        "rows": rows,
        "target_post": audit.target_post,
        "target_variables": audit.target_variables,
        "terminal_bases": audit.terminal_bases,
        "continued_goal_escapes": audit.continued_groups,
        "synthetic_goal_escapes": audit.synthetic_groups,
        "selected_goal_escapes": len(goal_escapes),
        "goal_parseable": audit.parseable,
        "seconds": time.perf_counter() - started,
    }


def _summary(rows: list[dict], task_rows: list[dict]) -> dict:
    scored = [row for row in rows if row["stratified_goal_score"] is not None]
    shaped = [{
        "task": (row["suite"], str(row["case_id"])),
        "suite": row["suite"],
        "case_id": str(row["case_id"]),
        "method": row["method"],
        "verified": bool(row["verified"]),
        "score": float(row["stratified_goal_score"]),
    } for row in scored]
    result = metrics(shaped)
    passed = [row for row in shaped if row["verified"]]
    failed = [row for row in shaped if not row["verified"]]
    full = [row for row in shaped if row["score"] == 1.0]
    result.update({
        "protocol": "two_family_stratified_goal_escape_prototype_v4",
        "score_weights": {
            "relation": _RELATION_WEIGHT,
            "dynamic_escape": _DYNAMIC_ESCAPE_WEIGHT,
            "goal_escape": _GOAL_ESCAPE_WEIGHT,
            "goal_hardness_power": _GOAL_HARDNESS_POWER,
        },
        "all_tasks": len(task_rows),
        "unscorable_tasks": sum(
            not any(row["negative_groups"] for row in task["rows"])
            for task in task_rows
        ),
        "tasks_with_goal_escape": sum(
            bool(task["rows"] and task["rows"][0]["goal_escape_groups"])
            for task in task_rows
        ),
        "unparseable_goal_tasks": sum(
            not task["goal_parseable"] for task in task_rows
        ),
        "verified_zero": sum(row["score"] == 0 for row in passed),
        "verified_zero_rate": (
            sum(row["score"] == 0 for row in passed) / len(passed)
        ),
        "failed_at_least_0_9": sum(row["score"] >= 0.9 for row in failed),
        "failed_at_least_0_9_rate": (
            sum(row["score"] >= 0.9 for row in failed) / len(failed)
        ),
        "full_score_failed": sum(not row["verified"] for row in full),
        "sampling_and_scoring_seconds": sum(
            task["seconds"] for task in task_rows
        ),
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = load_sample_manifest(args.root)
    tasks = discover_tasks()
    candidates = load_candidates()
    output = args.output or args.root / "goal_escape_v2_candidate_scores.jsonl"

    task_rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _score_task,
                task,
                manifest[(task.suite, task.case_id)],
                candidates[(task.suite, task.case_id)],
            ): task
            for task in tasks
        }
        for index, future in enumerate(as_completed(futures), 1):
            task_rows.append(future.result())
            if index % 25 == 0 or index == len(tasks):
                print(f"goal escape [{index}/{len(tasks)}]", flush=True)
    task_rows.sort(key=lambda row: (row["suite"], int(row["case_id"])))
    rows = [row for task in task_rows for row in task["rows"]]
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = _summary(rows, task_rows)
    summary_path = output.with_name(output.stem + "_summary.json")
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    print(output)
    print(summary_path)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
