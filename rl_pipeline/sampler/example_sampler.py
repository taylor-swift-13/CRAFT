"""
ExampleSampler — Component 1 (minimal).

The sampler sees ONLY the loop (it executes it) — never the assert/postcondition.

Running the loop from many inputs yields traces of loop-head valuations; their
union is the sampled reachable set. We produce:
  * positives : reachable loop-head valuations;
  * negatives : synthetic candidate traces designed to depart from sampled
    behavior, stored as WITNESS states grouped in `neg_groups`: a perturbation
    is a singleton
    ("real prefix + this state"); an escape continuation is one group.
    A rollout rejects a history iff some invariant is false at ANY witness.

Two complementary negative-candidate families:
  * relation : guard-preserving off-manifold perturbations around densely
    witnessed transitions and observed terminal transitions;
  * escape   : the body executed past an observed genuine exit.

Families have independent trace budgets (48 relation, 12 escape; 60 total).
Candidates are selected round-robin across structural buckets.  There is no
range family: a marginal bound alone must not dominate the quality score.

Conservative filters (a reachable state mislabeled as negative would distort
the tightness signal):
  * states observed reachable are never negatives;
  * states that could be a fresh loop ENTRY under their input are dropped;
  * oracle calls are determinized into sampled parameters and retained in each
    trace context, so relation candidates can use oracle-affected axes without
    conflating executions that chose different oracle values;
  * untracked persistent state and unsupported memory operations disable only
    synthetic relation perturbations; genuine post-exit continuations remain
    independently available.

Soundness of scoring is delegated to the reward's filter cascade, which ends
in real Houdini (Frama-C/WP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random
import re
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..common.program import (
    Program,
    bind_integer_constants,
    parse_program,
    state_external_integer_constants,
)
from ..common.state import State, eval_predicate
from . import cexec

# Canonical sampler defaults shared by reward-service and offline-scoring paths.
# Inference deliberately does not construct reward examples.
DEFAULT_N_RUNS = 12
DEFAULT_SEED = 0
NEGATIVE_SAMPLER_MODES = (
    "random",
    "structured",
)
# Bump whenever negative construction changes: persisted per-program coverage
# ledgers stamped with an older version are stale and must be regenerated.
NEGATIVE_SCHEMA_VERSION = 7

_SMALL_DELTAS = (1, -1, 2, -2)
# Terminal transitions are especially scarce in short loops.  Probe a slightly
# wider local neighborhood there so relation-breaking, guard-preserving
# witnesses are not lost when only a few terminal transitions are available.
_TERMINAL_DELTAS = (1, -1, 2, -2, 3, -3, 5, -5, 8, -8)
_BASE_CAP = 96           # perturbation bases, stratified across all positives
# Forward trace states required for a "dense" base: the entry state (it=0) and
# the first head (it=1) carry the SAME valuation, so a value-jump of 2 along a
# unit-step manifold is only witnessed by the state at it+3.
_DENSE_WINDOW = 3
_RELATION_GROUP_BUDGET = 48
_ESCAPE_GROUP_BUDGET = 12
_NEGATIVE_GROUP_BUDGET = (
    _RELATION_GROUP_BUDGET
    + _ESCAPE_GROUP_BUDGET
)


@dataclass(frozen=True)
class _NegativeCandidate:
    state: State
    bucket: Tuple
    # Smaller counterfactual changes stay nearer to the sampled manifold and
    # are therefore harder than distant perturbations in the same bucket.
    distance: int


@dataclass
class ExampleSet:
    program: Program
    positives: Dict[int, List[State]] = field(default_factory=dict)
    negatives: Dict[int, List[State]] = field(default_factory=dict)
    # witness-state indices per synthetic trace unit (see module docstring)
    neg_groups: Dict[int, List[List[int]]] = field(default_factory=dict)
    stats: Dict[int, dict] = field(default_factory=dict)
    # Kept after the historical fields for positional-constructor compatibility.
    negative_sampler: str = "structured"
    neg_group_families: Dict[int, List[str]] = field(default_factory=dict)

    def pos(self, loop_idx: int = 0) -> List[State]:
        return self.positives.get(loop_idx, [])

    def neg(self, loop_idx: int = 0) -> List[State]:
        return self.negatives.get(loop_idx, [])

    def groups(self, loop_idx: int = 0) -> List[List[int]]:
        g = self.neg_groups.get(loop_idx)
        if g is None:
            g = [[i] for i in range(len(self.neg(loop_idx)))]
        return g

    def group_families(self, loop_idx: int = 0) -> List[str]:
        families = self.neg_group_families.get(loop_idx)
        if families is None:
            return ["unknown"] * len(self.groups(loop_idx))
        if len(families) != len(self.groups(loop_idx)):
            raise ValueError(
                "negative trace group/family length mismatch: "
                f"{len(self.groups(loop_idx))} groups != {len(families)} families"
            )
        return families


class ExampleSampler:
    def __init__(
        self,
        source: str,
        n_runs: int = DEFAULT_N_RUNS,
        seed: int = DEFAULT_SEED,
        negative_sampler: str = "structured",
    ):
        if negative_sampler not in NEGATIVE_SAMPLER_MODES:
            raise ValueError(
                "negative_sampler must be one of: "
                + ", ".join(NEGATIVE_SAMPLER_MODES)
            )
        self.source = source
        self.n_runs = n_runs
        self.seed = seed
        self.negative_sampler = negative_sampler

    # ── positives ────────────────────────────────────────────────────────────
    @staticmethod
    def _dedup(states: List[State]) -> List[State]:
        seen, out = set(), []
        for s in states:
            # Pre and LoopEntry values are semantic inputs to labelled
            # predicates. The same current valuation reached from distinct
            # labelled states must retain every combination.
            k = s.key()
            if k not in seen:
                seen.add(k)
                out.append(s)
        return out

    # ── negatives ────────────────────────────────────────────────────────────
    @staticmethod
    def _modified_vars(prog: Program, loop_idx: int = 0) -> List[str]:
        """Loop-head variables the loop BODY assigns to — the ones that move
        along a trace, so perturbing them leaves the reachable set."""
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1: loop.body_close]
        mod = []
        for v in prog.pre_vars:
            e = re.escape(v)
            if (re.search(rf"\b{e}\s*(=[^=]|[-+*/%|&^]=)", body)
                    or re.search(rf"\b{e}\s*(\+\+|--)", body)
                    or re.search(rf"(\+\+|--)\s*\b{e}\b", body)):
                mod.append(v)
        return mod

    @staticmethod
    def _guard_nondeterministic(prog: Program, loop_idx: int = 0) -> bool:
        loop = prog.loops[loop_idx]
        return bool(re.search(r"\bunknown\w*\s*\(", loop.guard or ""))

    _ASSIGN_RE = re.compile(
        r"\b(\w+)\s*(?:=[^=]|[-+*/%|&^]=)|(?:\+\+|--)\s*(\w+)\b|\b(\w+)\s*(?:\+\+|--)")

    @classmethod
    def _assigned_names(cls, text: str) -> Set[str]:
        names: Set[str] = set()
        for m in cls._ASSIGN_RE.finditer(text):
            name = next((g for g in m.groups() if g), None)
            if name:
                names.add(name)
        return names

    @classmethod
    def _nondet_tainted(cls, prog: Program, loop_idx: int = 0) -> Set[str]:
        """Variables whose loop-body transition depends on ``unknown()``.

        Pre-loop oracle values are deliberately not tainted: once fixed for a
        sampled run, a transition such as ``x = x + 1`` is deterministic and
        can safely be perturbed. Taint starts only at oracle-dependent body
        assignments/branches and is propagated through body data flow.
        """
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1: loop.body_close]
        tainted: Set[str] = set()
        for m in re.finditer(r"\b(\w+)\s*=[^=][^;]*\bunknown\w*\s*\(", body):
            tainted.add(m.group(1))
        scopes = cls._branch_scopes(body)
        stmts = re.findall(r"\b(\w+)\s*=\s*([^=;][^;]*);", body)

        def refs_tainted(expr: str) -> bool:
            return any(re.search(rf"\b{re.escape(t)}\b", expr) for t in tainted)

        changed = True
        while changed:
            changed = False
            for cond, block in scopes:
                if re.search(r"\bunknown\w*\s*\(", cond) or refs_tainted(cond):
                    new = cls._assigned_names(block) - tainted
                    if new:
                        tainted |= new
                        changed = True
            for v, expr in stmts:
                if v not in tainted and refs_tainted(expr):
                    tainted.add(v)
                    changed = True
        return tainted

    @staticmethod
    def _branch_scope_ranges(body: str):
        """[(condition, [(start, end), ...])] for every `if (cond) {...}
        [else {...}]` in the body."""
        scopes = []
        for m in re.finditer(r"\bif\s*\(", body):
            depth, i = 1, m.end()
            while i < len(body) and depth:
                if body[i] == "(":
                    depth += 1
                elif body[i] == ")":
                    depth -= 1
                i += 1
            cond = body[m.end():i - 1]
            pos = body.find("{", i)
            if pos < 0 or body[i:pos].strip() not in ("", ")"):
                continue
            depth, j = 1, pos + 1
            while j < len(body) and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            ranges = [(pos + 1, j - 1)]
            m_else = re.match(r"\s*else\s*\{", body[j:])
            if m_else:
                epos = j + m_else.end() - 1
                depth, k = 1, epos + 1
                while k < len(body) and depth:
                    if body[k] == "{":
                        depth += 1
                    elif body[k] == "}":
                        depth -= 1
                    k += 1
                ranges.append((epos + 1, k - 1))
            scopes.append((cond, ranges))
        return scopes

    @classmethod
    def _branch_scopes(cls, body: str):
        """[(condition, joined block text)], PLUS braceless `if (cond) stmt;
        [else stmt2;]` arms."""
        scopes = [(cond, "\n".join(body[s:e] for s, e in ranges))
                  for cond, ranges in cls._branch_scope_ranges(body)]
        for m in re.finditer(r"\bif\s*\(", body):
            depth, i = 1, m.end()
            while i < len(body) and depth:
                if body[i] == "(":
                    depth += 1
                elif body[i] == ")":
                    depth -= 1
                i += 1
            cond = body[m.end():i - 1]
            rest = body[i:]
            if re.match(r"\s*\{", rest):
                continue
            stmt_end = body.find(";", i)
            if stmt_end < 0:
                continue
            arm = body[i:stmt_end + 1]
            m_else = re.match(r"\s*else\b(?!\s*if)", body[stmt_end + 1:])
            if m_else:
                epos = stmt_end + 1 + m_else.end()
                m_brace = re.match(r"\s*\{", body[epos:])
                if m_brace:
                    bpos = epos + m_brace.end() - 1
                    depth, k = 1, bpos + 1
                    while k < len(body) and depth:
                        if body[k] == "{":
                            depth += 1
                        elif body[k] == "}":
                            depth -= 1
                        k += 1
                    arm += "\n" + body[bpos + 1:k - 1]
                else:
                    e_end = body.find(";", epos)
                    if e_end > 0:
                        arm += "\n" + body[epos:e_end + 1]
            scopes.append((cond, arm))
        return scopes

    @staticmethod
    def _body_nondeterministic(prog: Program, loop_idx: int = 0) -> bool:
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1: loop.body_close]
        return bool(re.search(r"\bunknown\w*\s*\(", body))

    @classmethod
    def _untracked_body_state(cls, prog: Program, loop_idx: int = 0) -> Set[str]:
        """Names assigned in the body but absent from the loop-head valuation.

        Ordinary block-local temporaries are deliberately excluded: their
        lifetime ends before the next loop head, so they are transition-local
        values rather than omitted persistent state. A non-local assigned name
        that is absent from ``pre_vars`` still makes the projected trace
        incomplete, so finite perturbations cannot safely be labeled negative.
        """
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1:loop.body_close]
        body_locals: Set[str] = set()
        declaration = re.compile(
            r"\b(?P<storage>static\s+)?"
            r"(?:(?:unsigned|signed|long|short|const|volatile)\s+)*"
            r"(?:int|long|short|char|_Bool|unsigned|signed)\s+(?P<decls>[^;{}]+);"
        )
        for match in declaration.finditer(body):
            # A static block local persists between iterations and therefore
            # really is omitted loop-head state. Keep it in the unsafe set.
            if match.group("storage"):
                continue
            for declarator in match.group("decls").split(","):
                name = declarator.partition("=")[0].strip()
                if "*" in name or "[" in name:
                    continue
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    body_locals.add(name)
        return cls._assigned_names(body) - set(prog.pre_vars) - body_locals

    @staticmethod
    def _body_calls_function(prog: Program, loop_idx: int = 0) -> bool:
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1:loop.body_close]
        # ACSL annotations describe proof obligations, not executable calls.
        body = re.sub(r"/\*.*?\*/|//[^\n]*", " ", body, flags=re.DOTALL)
        calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body))
        language = {"if", "while", "for", "switch", "sizeof"}
        unsupported = {
            name for name in calls - language
            if not re.fullmatch(
                r"(?:unknown\w*|nondet\w*|__VERIFIER_nondet_\w+)",
                name,
            )
        }
        return bool(unsupported)

    @staticmethod
    def _body_has_unsupported_state(prog: Program, loop_idx: int = 0) -> bool:
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1:loop.body_close]
        return bool(re.search(
            r"\b(?:unsigned\s+|signed\s+)?(?:int|long|short)\s+"
            r"[^;{}]*(?:\*|\[)",
            body,
        ))

    @staticmethod
    def _bases(positives: List[State]) -> List[State]:
        """Subsample evenly inside every concrete trace/context.

        A single long run must not consume the global base cap: doing so loses
        the entry and terminal ranges of other inputs and turns parameterized
        bounds such as ``i <= n`` into effectively unsampled behavior.
        """
        if len(positives) <= _BASE_CAP:
            return positives
        traces: Dict[Tuple, List[State]] = {}
        for state in positives:
            key = (
                state.run,
                tuple(sorted(state.pre.items())),
                tuple(sorted(state.loop_entry.items())),
            )
            traces.setdefault(key, []).append(state)

        trace_values = list(traces.values())
        quotas = [0] * len(trace_values)
        remaining = min(_BASE_CAP, len(positives))
        active = list(range(len(trace_values)))
        while remaining and active:
            next_active = []
            for index in active:
                if remaining == 0:
                    break
                if quotas[index] < len(trace_values[index]):
                    quotas[index] += 1
                    remaining -= 1
                if quotas[index] < len(trace_values[index]):
                    next_active.append(index)
            active = next_active

        selected: List[State] = []
        for index, states in enumerate(trace_values):
            limit = quotas[index]
            if limit <= 0:
                continue
            if len(states) <= limit:
                selected.extend(states)
                continue
            # Preserve enough of the dense prefix to witness a local forward
            # transition, retain the terminal/end point for global coverage,
            # then spread the remaining quota across the whole trace.
            indices: List[int] = []

            def keep(position: int) -> None:
                if position not in indices and len(indices) < limit:
                    indices.append(position)

            for position in range(min(_DENSE_WINDOW + 1, limit)):
                keep(position)
            keep(len(states) - 1)
            if limit > 1:
                for slot in range(limit):
                    keep(round(slot * (len(states) - 1) / (limit - 1)))
            if len(indices) < limit:
                for position in range(len(states)):
                    keep(position)
                    if len(indices) == limit:
                        break
            selected.extend(states[position] for position in sorted(indices))
        return selected[:_BASE_CAP]

    def _entry_feasible_fn(self, prog: Program) -> Callable[[State], bool]:
        """A perturbed state that could be a FRESH LOOP ENTRY under its input is
        reachable and must never be labeled negative."""
        constants = state_external_integer_constants(prog)
        checks = [
            (n, bind_integer_constants(e, constants))
            for n, e in prog.local_inits
            if e and e.strip() and not re.search(r"\bunknown\w*\s*\(", e)
        ]
        req = bind_integer_constants(
            (prog.requires or "").strip(), constants
        )
        params = list(prog.params)

        def feasible(s: State) -> bool:
            for p in params:
                if p in s.pre and s.vars.get(p) != s.pre.get(p):
                    return False
            for name, expr in checks:
                ok = eval_predicate(f"({name}) == ({expr})", s)
                if ok is not True:
                    return False
            if req and eval_predicate(req, s) is False:
                return False
            return True

        return feasible

    def _relation_negatives(
        self,
        prog: Program,
        movable: List[str],
        bases: List[State],
        positives: List[State],
        raw_reach: List[State],
        capped: bool,
    ) -> List[_NegativeCandidate]:
        """Small off-manifold steps from locally witnessed trace positions.

        The original dense-window rule remains the default.  Short loops also
        contribute their terminal state when both ends of the genuine final
        transition were observed (predecessor guard true, terminal guard
        false).  This is what lets one-iteration loops produce relational
        witnesses without another execution.

        A relation candidate is admitted only when it stays inside the sampled
        envelope for the *same* Pre/LoopEntry context and preserves the guard
        truth value.  Reachability filtering later removes points already on
        the joint trace manifold.  Consequently a simple marginal bound cannot
        earn relation credit, and no range/control fallback contaminates this
        family.
        """
        out: List[_NegativeCandidate] = []
        trace_index = {
            (s.run, s.it): s for s in raw_reach if s.run >= 0
        }
        dense_index = set(trace_index)
        guard = bind_integer_constants(
            prog.loops[0].guard or "",
            state_external_integer_constants(prog),
        )

        by_context: Dict[Tuple, List[State]] = {}
        for state in positives:
            by_context.setdefault(state.context_key(), []).append(state)

        # Per Pre/LoopEntry context: variable -> (min, max) over its positives.
        envelopes: Dict[Tuple, Dict[str, Tuple[int, int]]] = {}
        for context, states in by_context.items():
            names = set.intersection(*(set(state.vars) for state in states))
            envelopes[context] = {
                name: (
                    min(state.vars[name] for state in states),
                    max(state.vars[name] for state in states),
                )
                for name in names
            }

        def is_terminal(r: State) -> bool:
            if capped or r.run < 0 or r.it <= 0:
                return False
            predecessor = trace_index.get((r.run, r.it - 1))
            if predecessor is None:
                return False
            return (
                eval_predicate(guard, predecessor) is True
                and eval_predicate(guard, r) is False
            )

        # `_bases` is globally capped and can skip the last state of a run.
        # Explicitly retain witnessed terminals so short traces are represented.
        relation_bases = list(bases)
        base_keys = {s.key() for s in relation_bases}
        if not capped:
            for state in raw_reach:
                if state.key() not in base_keys and is_terminal(state):
                    relation_bases.append(state)
                    base_keys.add(state.key())

        def transition_step(r: State) -> Dict[str, int]:
            """The locally witnessed transition at base ``r`` (forward step,
            or the final transition at a terminal head); {} when unknown."""
            neighbor = trace_index.get((r.run, r.it + 1))
            if neighbor is not None:
                return {
                    name: neighbor.vars[name] - r.vars[name]
                    for name in r.vars
                    if name in neighbor.vars
                }
            predecessor = trace_index.get((r.run, r.it - 1))
            if predecessor is None:
                return {}
            return {
                name: r.vars[name] - predecessor.vars[name]
                for name in r.vars
                if name in predecessor.vars
            }

        def add_candidate(
            r: State,
            nv: Dict[str, int],
            axes: Tuple[str, ...],
            directions: Tuple[int, ...],
            terminal: bool,
            base_guard: Optional[bool],
            envelope: Dict[str, Tuple[int, int]],
            step: Dict[str, int],
        ) -> None:
            state = r.with_vars(nv)
            inside_envelope = all(
                name in envelope
                and envelope[name][0] <= value <= envelope[name][1]
                for name, value in nv.items()
            )
            candidate_guard = eval_predicate(guard, state)
            same_guard = (
                base_guard is not None
                and candidate_guard is not None
                and base_guard == candidate_guard
            )
            if not inside_envelope or not same_guard:
                return

            # A state predicate cannot reject a counterfactual point that is
            # merely another position on the same locally observed trajectory.
            # In particular, for ``x++`` every in-range scalar perturbation is
            # reachable at another iteration. Reject perturbation vectors that
            # are an integer multiple of the witnessed forward transition (or
            # of the final transition at a terminal head).
            multiplier = None
            on_transition_tangent = bool(step)
            for name, step_value in step.items():
                change = nv[name] - r.vars[name]
                if step_value == 0:
                    if change != 0:
                        on_transition_tangent = False
                        break
                    continue
                if change % step_value:
                    on_transition_tangent = False
                    break
                ratio = change // step_value
                if multiplier is None:
                    multiplier = ratio
                elif ratio != multiplier:
                    on_transition_tangent = False
                    break
            if on_transition_tangent and multiplier is not None:
                return

            out.append(_NegativeCandidate(
                state=state,
                bucket=(
                    "terminal" if terminal else "interior",
                    axes,
                    directions,
                ),
                distance=sum(abs(nv[name] - r.vars[name]) for name in axes),
            ))

        for r in relation_bases:
            terminal = is_terminal(r)
            dense = r.run < 0 or all(
                (r.run, r.it + k) in dense_index
                for k in range(1, _DENSE_WINDOW + 1)
            )
            if not dense and not terminal:
                continue
            base = r.vars
            # Every base is a positive, so its context always has an envelope;
            # the witnessed transition step is likewise fixed per base.
            base_guard = eval_predicate(guard, r)
            envelope = envelopes[r.context_key()]
            step = transition_step(r)
            deltas = _TERMINAL_DELTAS if terminal else _SMALL_DELTAS
            for v in movable:
                for d in deltas:
                    nv = dict(base)
                    nv[v] += d
                    add_candidate(
                        r, nv, (v,), (1 if d > 0 else -1,), terminal,
                        base_guard, envelope, step,
                    )
            for i in range(len(movable)):
                for j in range(i + 1, len(movable)):
                    u, w = movable[i], movable[j]
                    for d in deltas:
                        for su, sw in ((d, d), (d, -d)):
                            nv = dict(base)
                            nv[u] += su
                            nv[w] += sw
                            add_candidate(
                                r,
                                nv,
                                (u, w),
                                (
                                    1 if su > 0 else -1,
                                    1 if sw > 0 else -1,
                                ),
                                terminal,
                                base_guard,
                                envelope,
                                step,
                            )
        return out

    @staticmethod
    def _round_robin(
        candidates: List[_NegativeCandidate], limit: int
    ) -> List[_NegativeCandidate]:
        """Stable round-robin selection across buckets, nearest candidates first."""
        if limit <= 0 or not candidates:
            return []
        buckets: Dict[Tuple, List[_NegativeCandidate]] = {}
        for candidate in candidates:
            buckets.setdefault(candidate.bucket, []).append(candidate)
        for values in buckets.values():
            values.sort(key=lambda candidate: candidate.distance)
        positions = {bucket: 0 for bucket in buckets}
        selected: List[_NegativeCandidate] = []
        active = list(buckets)
        while active and len(selected) < limit:
            next_active = []
            for bucket in active:
                position = positions[bucket]
                values = buckets[bucket]
                if position < len(values):
                    selected.append(values[position])
                    positions[bucket] = position + 1
                    if len(selected) == limit:
                        break
                if positions[bucket] < len(values):
                    next_active.append(bucket)
            active = next_active
        return selected

    def _negatives(self, prog: Program, positives: List[State], overrun: List[State],
                   raw_reach: List[State], capped: bool,
                   analysis_prog: Program | None = None,
                   ) -> Tuple[List[State], List[List[int]], List[str], dict]:
        # ``prog`` is the determinized execution program, which supplies exact
        # entry constraints for fresh oracle parameters. Safety checks still
        # inspect the original body so oracle-tainted variables stay immovable.
        analysis_prog = analysis_prog or prog
        movable = self._modified_vars(analysis_prog, 0)
        if not movable:
            movable = [
                v for v in analysis_prog.pre_vars
                if v not in set(analysis_prog.params)
            ] or list(analysis_prog.pre_vars)

        # Reachability is relative to the same function-entry and loop-entry
        # snapshots. A current valuation reached under another input is not a
        # reachable counterexample to a labelled invariant for this input.
        reachable = {s.key() for s in positives}
        entry_feasible = self._entry_feasible_fn(prog)
        seen: set = set()

        def select_candidates(
            candidates: List[_NegativeCandidate],
            limit: int,
        ) -> List[State]:
            """Filter, stratify, and commit only candidates that are emitted."""
            admissible: List[_NegativeCandidate] = []
            local_seen: Set[Tuple] = set()
            for candidate in candidates:
                state = candidate.state
                key = state.key()
                if key in reachable or key in seen or key in local_seen:
                    continue
                if entry_feasible(state):
                    continue
                local_seen.add(key)
                admissible.append(candidate)
            selected = self._round_robin(admissible, limit)
            states = [candidate.state for candidate in selected]
            seen.update(state.key() for state in states)
            return states

        def select_escape_groups() -> List[List[State]]:
            """Keep genuine continuations atomic and cap them in trace units."""
            by_run: Dict[int, List[State]] = {}
            for state in overrun:
                by_run.setdefault(state.run, []).append(state)
            selected_groups: List[List[State]] = []
            for run in sorted(by_run):
                if len(selected_groups) >= _ESCAPE_GROUP_BUDGET:
                    break
                group: List[State] = []
                local_seen: Set[Tuple] = set()
                for state in by_run[run]:
                    key = state.key()
                    if key in reachable or key in seen or key in local_seen:
                        continue
                    if entry_feasible(state):
                        continue
                    local_seen.add(key)
                    group.append(state)
                if not group:
                    continue
                selected_groups.append(group)
                seen.update(state.key() for state in group)
            return selected_groups

        nondet_body = self._body_nondeterministic(analysis_prog, 0)
        untracked = self._untracked_body_state(analysis_prog, 0)
        body_call = self._body_calls_function(analysis_prog, 0)
        unsupported_state = self._body_has_unsupported_state(analysis_prog, 0)
        tainted = self._nondet_tainted(analysis_prog, 0)
        guard = analysis_prog.loops[0].guard or ""
        nondet = (self._guard_nondeterministic(analysis_prog, 0)
                  or any(re.search(rf"\b{re.escape(t)}\b", guard) for t in tainted))
        bases = self._bases(positives)

        # Oracle call sites have already become sampled parameters in ``prog``;
        # those parameter values are present in Pre/LoopEntry and therefore
        # separate trace contexts.  Do not discard every variable controlled by
        # an oracle branch: doing so erased all signal on 108 Full-832 tasks.
        # Incomplete persistent/memory state still blocks arbitrary relation
        # perturbations, but it does not invalidate a concretely executed
        # post-exit continuation.
        relation_blocked = bool(untracked) or body_call or unsupported_state
        relation_blockers: List[str] = []
        if untracked:
            relation_blockers.append("persistent_untracked_state")
        if body_call:
            relation_blockers.append("body_call")
        if unsupported_state:
            relation_blockers.append("unsupported_state")

        # Cross-family de-duplication follows the two-family semantic order:
        # relation > escape. Each family keeps its own budget.
        relation: List[State] = []
        escape_groups: List[List[State]] = []
        random_states: List[State] = []
        if self.negative_sampler == "random":
            if not relation_blocked:
                # Budget-matched unstructured baseline.  It uses the same
                # reachable bases, context-preserving axes, entry filter, seed,
                # and total trace cap as the structured sampler, but chooses one
                # or two axes and local deltas uniformly.
                rng = random.Random(self.seed ^ 0x4C4F4F50)
                deltas = tuple(range(-34, 0)) + tuple(range(1, 35))
                attempts = 0
                max_attempts = max(1024, 100 * _NEGATIVE_GROUP_BUDGET)
                while (
                    bases
                    and movable
                    and len(random_states) < _NEGATIVE_GROUP_BUDGET
                    and attempts < max_attempts
                ):
                    attempts += 1
                    base = rng.choice(bases)
                    n_axes = 1 if len(movable) == 1 else rng.choice((1, 2))
                    axes = rng.sample(movable, n_axes)
                    values = dict(base.vars)
                    for variable in axes:
                        values[variable] += rng.choice(deltas)
                    state = base.with_vars(values)
                    key = state.key()
                    if key in reachable or key in seen or entry_feasible(state):
                        continue
                    seen.add(key)
                    random_states.append(state)
        else:  # structured
            if not relation_blocked:
                relation = select_candidates(
                    self._relation_negatives(
                        prog,
                        movable,
                        bases,
                        positives,
                        raw_reach,
                        capped,
                    ),
                    _RELATION_GROUP_BUDGET,
                )
            # Escape is generated from a real exit and an actually executed
            # continuation, so it remains available even when arbitrary state
            # perturbation is conservatively disabled.
            escape_groups = select_escape_groups()

        negatives: List[State] = []
        groups: List[List[int]] = []
        group_families: List[str] = []
        for state in relation:
            groups.append([len(negatives)])
            group_families.append("relation")
            negatives.append(state)
        for trace in escape_groups:
            indices = []
            for state in trace:
                indices.append(len(negatives))
                negatives.append(state)
            groups.append(indices)
            group_families.append("escape")
        for state in random_states:
            groups.append([len(negatives)])
            group_families.append("random")
            negatives.append(state)
        zero_blockers: List[str] = []
        if not groups:
            zero_blockers.extend(relation_blockers)
            if not relation:
                zero_blockers.append("no_admissible_relation_trace")
            if not escape_groups:
                zero_blockers.append("no_admissible_escape_trace")

        stats = {
            "n_traces": len(groups),
            "n_witness_states": len(negatives),
            "relation": len(relation),
            "escape": len(escape_groups),
            "random": len(random_states),
            "negative_sampler": self.negative_sampler,
            "negative_budget": _NEGATIVE_GROUP_BUDGET,
            "relation_budget": _RELATION_GROUP_BUDGET,
            "escape_budget": _ESCAPE_GROUP_BUDGET,
            "capped": capped,
            "nondet_guard": nondet,
            "nondet_body": nondet_body,
            "untracked_state": sorted(untracked),
            "body_call": body_call,
            "unsupported_state": unsupported_state,
            "safe_movable": sorted(movable),
            "relation_movable": sorted(movable),
            "relation_blockers": relation_blockers,
            "tainted_persistent": sorted(
                set(analysis_prog.pre_vars) & tainted
            ),
            "tainted_relation_axes": sorted(
                set(movable) & tainted
            ),
            "zero_blockers": zero_blockers,
        }
        return negatives, groups, group_families, stats

    # ── driver ───────────────────────────────────────────────────────────────
    @staticmethod
    def _determinize_source(source: str) -> str:
        """Replace oracle calls with fresh integer parameters for sampling.

        This transformation is used only by the concrete executor. The
        original source remains the program exposed to invariant generation
        and Houdini verification.
        """
        # Match on a same-length code mask so comments and string literals are
        # never rewritten as oracle calls.
        def blank(match: re.Match) -> str:
            return re.sub(r"[^\n]", " ", match.group(0))

        mask = re.sub(
            r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
            blank,
            source,
            flags=re.DOTALL,
        )
        call_re = re.compile(r"\bunknown\w*\s*\(\s*\)")
        occupied = set(re.findall(r"\b[A-Za-z_]\w*\b", mask))
        replacements: List[Tuple[int, int, str]] = []
        params: List[str] = []
        next_index = 0

        for match in call_re.finditer(mask):
            # Do not mistake old-style declarations/definitions such as
            # ``int unknown();`` for calls.
            boundary = max(
                mask.rfind(";", 0, match.start()),
                mask.rfind("{", 0, match.start()),
                mask.rfind("}", 0, match.start()),
                mask.rfind("\n", 0, match.start()),
            )
            prefix = mask[boundary + 1:match.start()].strip()
            declaration_prefix = re.fullmatch(
                r"(?:(?:extern|static|inline|const|volatile|signed|unsigned|long|short)\s+)*"
                r"(?:void|int|char|_Bool|float|double)(?:\s+long)?",
                prefix,
            )
            suffix = mask[match.end():].lstrip()
            if declaration_prefix and suffix.startswith((";", "{")):
                continue
            if prefix.startswith("#"):
                continue

            while f"_nd{next_index}" in occupied:
                next_index += 1
            name = f"_nd{next_index}"
            next_index += 1
            occupied.add(name)
            params.append(name)
            replacements.append((match.start(), match.end(), name))

        if not replacements:
            return source

        pieces: List[str] = []
        cursor = 0
        for start, end, name in replacements:
            pieces.extend((source[cursor:start], name))
            cursor = end
        pieces.append(source[cursor:])
        determinized = "".join(pieces)

        # Find the loop-containing function definition selected by the parser,
        # rather than preceding oracle declarations or helper functions.
        func_name = parse_program(source).func_name
        det_mask = re.sub(
            r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
            blank,
            determinized,
            flags=re.DOTALL,
        )
        signature = None
        for match in re.finditer(rf"\b{re.escape(func_name)}\s*\(", det_mask):
            open_paren = det_mask.find("(", match.start(), match.end())
            depth = 1
            close_paren = open_paren + 1
            while close_paren < len(det_mask) and depth:
                if det_mask[close_paren] == "(":
                    depth += 1
                elif det_mask[close_paren] == ")":
                    depth -= 1
                close_paren += 1
            if depth:
                continue
            close_paren -= 1
            after = close_paren + 1
            while after < len(det_mask) and det_mask[after].isspace():
                after += 1
            if after < len(det_mask) and det_mask[after] == "{":
                signature = (open_paren, close_paren)
                break
        if signature is None:
            raise ValueError(f"cannot determinize function signature: {func_name}")

        open_paren, close_paren = signature
        existing = determinized[open_paren + 1:close_paren].strip()
        added = ", ".join(f"int {name}" for name in params)
        if not existing or existing == "void":
            return determinized[:open_paren + 1] + added + determinized[close_paren:]
        return determinized[:close_paren] + ", " + added + determinized[close_paren:]

    def sample(self) -> ExampleSet:
        prog = parse_program(self.source)
        es = ExampleSet(program=prog, negative_sampler=self.negative_sampler)
        runs = self.n_runs * 2 if self._body_nondeterministic(prog, 0) else self.n_runs
        sampling_prog = parse_program(self._determinize_source(self.source))
        reach, overrun, capped, execution_stats = cexec.collect_traces(
            sampling_prog, loop_idx=0, n_runs=runs, seed=self.seed,
            return_stats=True,
        )
        positives = self._dedup(reach)
        negatives, groups, group_families, stats = self._negatives(
            sampling_prog, positives, overrun, reach, capped,
            analysis_prog=prog,
        )
        es.positives[0] = positives
        es.negatives[0] = negatives
        es.neg_groups[0] = groups
        es.neg_group_families[0] = group_families
        es.stats[0] = {
            "n_pos": len(positives),
            "n_neg": len(groups),
            **execution_stats,
            **stats,
        }
        return es


def _cli():
    import argparse
    import logging

    ap = argparse.ArgumentParser(description="Sample positive/negative loop-entry valuations")
    ap.add_argument("program", help="path to a C program")
    ap.add_argument("--runs", type=int, default=DEFAULT_N_RUNS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument(
        "--negative-sampler",
        choices=NEGATIVE_SAMPLER_MODES,
        default="structured",
    )
    ap.add_argument("--show", type=int, default=6, help="print N example states each")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    src = open(args.program).read()
    es = ExampleSampler(
        src,
        n_runs=args.runs,
        seed=args.seed,
        negative_sampler=args.negative_sampler,
    ).sample()
    print(
        f"program: {es.program.func_name}  guard: {es.program.loop.guard!r} "
        f"sampler={es.negative_sampler} (loop only; assert not used)"
    )
    for li in sorted(es.positives):
        st = es.stats[li]
        print(f"\nloop {li}: positives={st['n_pos']} negative-traces={st['n_neg']} "
              f"(relation={st.get('relation','-')} escape={st.get('escape','-')} "
              f"random={st.get('random','-')} capped={st.get('capped','-')})")
        print("  positives:")
        for s in es.pos(li)[:args.show]:
            print("    +", s.render())
        print("  negatives:")
        for s in es.neg(li)[:args.show]:
            print("    -", s.render())


if __name__ == "__main__":
    _cli()
