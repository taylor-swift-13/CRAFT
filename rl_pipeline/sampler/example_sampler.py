"""
ExampleSampler — Component 1 (minimal).

The sampler sees ONLY the loop (it executes it) — never the assert/postcondition.

Running the loop from many inputs yields traces of loop-head valuations; their
union is the sampled reachable set. We produce:
  * positives : reachable loop-head valuations;
  * negatives : synthetic candidate traces designed to depart from sampled
    behavior, stored as WITNESS states grouped in `neg_groups`: a perturbation
    is a singleton
    ("real prefix + this state"); an over-run continuation is one group.
    A rollout rejects a history iff some invariant is false at ANY witness.

Four negative-candidate families:
  * relation : small perturbations (±1, ±2) around DENSE sampled bases and
    observed terminal transitions;
  * over-run : the body executed past an observed genuine exit;
  * escape   : the nearest ladder step in each direction that leaves the
    variable's sampled range.
  * frame    : perturb a variable that the loop never writes while preserving
    its exact Pre/LoopEntry snapshot.

Families have independent trace budgets (320 relation, 64 over-run, 128
escape, 64 frame). Candidates are selected round-robin across structural
buckets, so a large collection of easy range/frame violations cannot crowd
relational witnesses out of the score.

Conservative filters (a reachable state mislabeled as negative would distort
the tightness signal):
  * states observed reachable are never negatives;
  * states that could be a fresh loop ENTRY under their input are dropped;
  * oracle calls are determinized into sampled parameters; variables whose body
    transition remains oracle-tainted are not perturbed, and negatives are
    disabled only when no safe movable variable remains;
  * capped runs disable escapes.

Soundness of scoring is delegated to the reward's filter cascade, which ends
in real Houdini (Frama-C/WP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random
import re
from typing import Callable, Dict, List, Set, Tuple

from ..common.program import Program, parse_program
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

_SMALL_DELTAS = (1, -1, 2, -2)
# Terminal transitions are especially scarce in short loops.  Probe a slightly
# wider local neighborhood there so relation-breaking, guard-preserving
# witnesses are not outnumbered by easy control/range violations.
_TERMINAL_DELTAS = (1, -1, 2, -2, 3, -3, 5, -5, 8, -8)
_LADDER_DELTAS = (5, -5, 8, -8, 13, -13, 21, -21, 34, -34)
_BASE_CAP = 96           # perturbation bases, stratified across all positives
# Forward trace states required for a "dense" base: the entry state (it=0) and
# the first head (it=1) carry the SAME valuation, so a value-jump of 2 along a
# unit-step manifold is only witnessed by the state at it+3.
_DENSE_WINDOW = 3
_RELATION_GROUP_BUDGET = 320
# Non-preferred relation candidates change the guard truth value or leave the
# sampled envelope, so single-variable bounds reject them too easily.  They
# remain useful for coverage, but may not fill unused hard-relation capacity.
_RELATION_FALLBACK_BUDGET = 64
_OVERRUN_GROUP_BUDGET = 64
_ESCAPE_GROUP_BUDGET = 128
_FRAME_GROUP_BUDGET = 64
_NEGATIVE_GROUP_BUDGET = (
    _RELATION_GROUP_BUDGET
    + _OVERRUN_GROUP_BUDGET
    + _ESCAPE_GROUP_BUDGET
    + _FRAME_GROUP_BUDGET
)


@dataclass(frozen=True)
class _NegativeCandidate:
    state: State
    bucket: Tuple
    preferred: bool = True


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

    def pos(self, loop_idx: int = 0) -> List[State]:
        return self.positives.get(loop_idx, [])

    def neg(self, loop_idx: int = 0) -> List[State]:
        return self.negatives.get(loop_idx, [])

    def groups(self, loop_idx: int = 0) -> List[List[int]]:
        g = self.neg_groups.get(loop_idx)
        if g is None:
            g = [[i] for i in range(len(self.neg(loop_idx)))]
        return g


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
        return bool(calls - {
            "if", "while", "for", "switch", "sizeof",
            "unknown", "unknown1", "unknown2", "unknown3", "nondet",
            "__VERIFIER_nondet_int", "__VERIFIER_nondet_uint",
        })

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
        """Stratified subsample across ALL positives."""
        if len(positives) <= _BASE_CAP:
            return positives
        step = len(positives) // _BASE_CAP
        return positives[::step][:_BASE_CAP]

    def _entry_feasible_fn(self, prog: Program) -> Callable[[State], bool]:
        """A perturbed state that could be a FRESH LOOP ENTRY under its input is
        reachable and must never be labeled negative."""
        checks = [(n, e) for n, e in prog.local_inits
                  if e and e.strip() and not re.search(r"\bunknown\w*\s*\(", e)]
        req = (prog.requires or "").strip()
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

        Candidates that stay inside every sampled variable envelope and keep
        the guard truth value are selected before range/control-changing
        fallbacks.  They force the score to distinguish loop relations instead
        of rewarding only easy extrema.
        """
        out: List[_NegativeCandidate] = []
        trace_index = {
            (s.run, s.it): s for s in raw_reach if s.run >= 0
        }
        dense_index = set(trace_index)
        guard = prog.loops[0].guard or ""
        lo = {
            v: min(p.vars[v] for p in positives)
            for v in prog.pre_vars
            if all(v in p.vars for p in positives)
        }
        hi = {
            v: max(p.vars[v] for p in positives)
            for v in prog.pre_vars
            if all(v in p.vars for p in positives)
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

        def add_candidate(
            r: State,
            nv: Dict[str, int],
            axes: Tuple[str, ...],
            directions: Tuple[int, ...],
            terminal: bool,
        ) -> None:
            state = State(
                vars=nv,
                pre=dict(r.pre),
                loop_entry=dict(r.loop_entry),
            )
            base_guard = eval_predicate(guard, r)
            candidate_guard = eval_predicate(guard, state)
            inside_envelope = all(
                name in lo and lo[name] <= value <= hi[name]
                for name, value in state.vars.items()
            )
            same_guard = (
                base_guard is not None
                and candidate_guard is not None
                and base_guard == candidate_guard
            )
            out.append(_NegativeCandidate(
                state=state,
                bucket=(
                    "terminal" if terminal else "interior",
                    axes,
                    directions,
                ),
                preferred=inside_envelope and same_guard,
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
            deltas = _TERMINAL_DELTAS if terminal else _SMALL_DELTAS
            for v in movable:
                for d in deltas:
                    nv = dict(base)
                    nv[v] += d
                    add_candidate(
                        r, nv, (v,), (1 if d > 0 else -1,), terminal
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
                            )
        return out

    def _escape_negatives(self, movable: List[str], bases: List[State],
                          positives: List[State]) -> List[_NegativeCandidate]:
        """Return one nearest outside-envelope step per base/axis/direction."""
        lo = {v: min(p.vars[v] for p in positives) for v in movable}
        hi = {v: max(p.vars[v] for p in positives) for v in movable}
        out: List[_NegativeCandidate] = []
        for r in bases:
            base = r.vars
            for v in movable:
                for direction in (1, -1):
                    outside = [
                        d for d in _LADDER_DELTAS
                        if (1 if d > 0 else -1) == direction
                        and not lo[v] <= base[v] + d <= hi[v]
                    ]
                    if not outside:
                        continue
                    d = min(outside, key=abs)
                    nv = dict(base)
                    nv[v] = base[v] + d
                    out.append(_NegativeCandidate(
                        state=State(
                            vars=nv,
                            pre=dict(r.pre),
                            loop_entry=dict(r.loop_entry),
                        ),
                        bucket=(v, direction),
                    ))
        return out

    def _frame_negatives(
        self,
        prog: Program,
        bases: List[State],
        loop_idx: int = 0,
    ) -> List[_NegativeCandidate]:
        """Break variables that the loop transition never assigns.

        These witnesses remain unreachable even when execution is capped or
        oracle-controlled: for a fixed ``LoopEntry`` snapshot, a variable that
        neither the guard nor body writes must retain that value at every loop
        head. They are kept in a separate family because frame discrimination
        is easier than discovering the loop's changing-state relation.
        """
        loop = prog.loops[loop_idx]
        body = prog.source[loop.body_open + 1:loop.body_close]
        assigned = self._assigned_names(body) | self._assigned_names(loop.guard or "")
        framed = [name for name in prog.pre_vars if name not in assigned]
        unsigned = set(prog.unsigned_vars)
        out: List[_NegativeCandidate] = []
        for base in bases:
            for name in framed:
                if name not in base.vars or name not in base.loop_entry:
                    continue
                # Only use a witnessed frame equality. This also avoids treating
                # a pre-loop initialization as though it happened in the loop.
                if base.vars[name] != base.loop_entry[name]:
                    continue
                deltas = (1, 2) if name in unsigned else (1, -1)
                for delta in deltas:
                    values = dict(base.vars)
                    values[name] += delta
                    out.append(_NegativeCandidate(
                        state=State(
                            vars=values,
                            pre=dict(base.pre),
                            loop_entry=dict(base.loop_entry),
                        ),
                        bucket=(name, 1 if delta > 0 else -1),
                    ))
        return out

    @staticmethod
    def _round_robin(
        candidates: List[_NegativeCandidate], limit: int
    ) -> List[_NegativeCandidate]:
        """Stable round-robin selection across candidate buckets."""
        if limit <= 0 or not candidates:
            return []
        buckets: Dict[Tuple, List[_NegativeCandidate]] = {}
        for candidate in candidates:
            buckets.setdefault(candidate.bucket, []).append(candidate)
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
                   ) -> Tuple[List[State], List[List[int]], dict]:
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
            fallback_limit: int | None = None,
            filter_fresh_entry: bool = True,
        ) -> List[State]:
            """Filter, stratify, and commit only candidates that are emitted."""
            selected: List[_NegativeCandidate] = []
            local_seen: Set[Tuple] = set()
            for preferred in (True, False):
                admissible: List[_NegativeCandidate] = []
                for candidate in candidates:
                    if candidate.preferred != preferred:
                        continue
                    state = candidate.state
                    key = state.key()
                    if key in reachable or key in seen or key in local_seen:
                        continue
                    if filter_fresh_entry and entry_feasible(state):
                        continue
                    local_seen.add(key)
                    admissible.append(candidate)
                remaining = limit - len(selected)
                if remaining <= 0:
                    break
                tier_limit = remaining
                if not preferred and fallback_limit is not None:
                    tier_limit = min(tier_limit, fallback_limit)
                chosen = self._round_robin(admissible, tier_limit)
                selected.extend(chosen)
                # If the tier overflowed its budget, the family is full.
                if len(chosen) < len(admissible) and len(selected) == limit:
                    break
            states = [candidate.state for candidate in selected]
            seen.update(state.key() for state in states)
            return states

        def select_overrun_groups() -> List[List[State]]:
            """Keep genuine continuations atomic and cap them in trace units."""
            by_run: Dict[int, List[State]] = {}
            for state in overrun:
                by_run.setdefault(state.run, []).append(state)
            selected_groups: List[List[State]] = []
            for run in sorted(by_run):
                if len(selected_groups) >= _OVERRUN_GROUP_BUDGET:
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
        movable = [v for v in movable if v not in tainted]

        # Incomplete transition state/control makes projected perturbations
        # unreliable. Degrade to no synthetic negatives in that case.
        uncontrolled = (
            bool(untracked) or body_call or unsupported_state
            or ((nondet or nondet_body) and not movable)
        )
        zero_blockers: List[str] = []
        if untracked:
            zero_blockers.append("persistent_untracked_state")
        if body_call:
            zero_blockers.append("body_call")
        if unsupported_state:
            zero_blockers.append("unsupported_state")
        if (nondet or nondet_body) and not movable:
            zero_blockers.append("nondeterministic_no_safe_axis")

        # Family priority for cross-family de-duplication is intentional:
        # relation > over-run > escape > frame. Each family keeps its own
        # budget; an under-filled easy family never consumes relational
        # capacity.
        relation: List[State] = []
        overrun_groups: List[List[State]] = []
        escape: List[State] = []
        frame: List[State] = []
        random_states: List[State] = []
        if not uncontrolled:
            if self.negative_sampler == "random":
                # Budget-matched unstructured baseline.  It uses the same
                # reachable bases, movable-variable safety checks, entry
                # filter, seed, and total trace cap as the structured sampler,
                # but
                # chooses one or two axes and local deltas uniformly.
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
                    state = State(
                        vars=values,
                        pre=dict(base.pre),
                        loop_entry=dict(base.loop_entry),
                    )
                    key = state.key()
                    if key in reachable or key in seen or entry_feasible(state):
                        continue
                    seen.add(key)
                    random_states.append(state)
            else:  # structured
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
                    fallback_limit=_RELATION_FALLBACK_BUDGET,
                )
                overrun_groups = select_overrun_groups()
                if not capped and positives:
                    escape = select_candidates(
                        self._escape_negatives(movable, bases, positives),
                        _ESCAPE_GROUP_BUDGET,
                    )
        # Frame witnesses do not depend on termination or on a deterministic
        # update axis, so they remain available when nondeterminism blocks the
        # other structured families. Hidden state/calls can invalidate the
        # projected no-write argument, hence the state-completeness blockers.
        if (
            self.negative_sampler == "structured"
            and not (untracked or body_call or unsupported_state)
        ):
            frame = select_candidates(
                self._frame_negatives(analysis_prog, bases),
                _FRAME_GROUP_BUDGET,
                filter_fresh_entry=False,
            )

        negatives: List[State] = []
        groups: List[List[int]] = []
        for state in relation:
            groups.append([len(negatives)])
            negatives.append(state)
        for trace in overrun_groups:
            indices = []
            for state in trace:
                indices.append(len(negatives))
                negatives.append(state)
            groups.append(indices)
        for state in escape:
            groups.append([len(negatives)])
            negatives.append(state)
        for state in random_states:
            groups.append([len(negatives)])
            negatives.append(state)
        for state in frame:
            groups.append([len(negatives)])
            negatives.append(state)
        if groups:
            zero_blockers = []

        stats = {
            "n_traces": len(groups),
            "n_witness_states": len(negatives),
            "relation": len(relation),
            "bound_overrun": len(overrun_groups),
            "bound_escape": len(escape),
            "random": len(random_states),
            "frame": len(frame),
            "negative_sampler": self.negative_sampler,
            "negative_budget": _NEGATIVE_GROUP_BUDGET,
            "relation_budget": _RELATION_GROUP_BUDGET,
            "relation_fallback_budget": _RELATION_FALLBACK_BUDGET,
            "overrun_budget": _OVERRUN_GROUP_BUDGET,
            "escape_budget": _ESCAPE_GROUP_BUDGET,
            "frame_budget": _FRAME_GROUP_BUDGET,
            "capped": capped,
            "nondet_guard": nondet,
            "nondet_body": nondet_body,
            "untracked_state": sorted(untracked),
            "body_call": body_call,
            "unsupported_state": unsupported_state,
            "safe_movable": sorted(movable),
            "tainted_persistent": sorted(
                set(analysis_prog.pre_vars) & tainted
            ),
            "zero_blockers": zero_blockers,
        }
        return negatives, groups, stats

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
        negatives, groups, stats = self._negatives(
            sampling_prog, positives, overrun, reach, capped,
            analysis_prog=prog,
        )
        es.positives[0] = positives
        es.negatives[0] = negatives
        es.neg_groups[0] = groups
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
              f"(relation={st.get('relation','-')} overrun={st.get('bound_overrun','-')} "
              f"escape={st.get('bound_escape','-')} random={st.get('random','-')} "
              f"capped={st.get('capped','-')})")
        print("  positives:")
        for s in es.pos(li)[:args.show]:
            print("    +", s.render())
        print("  negatives:")
        for s in es.neg(li)[:args.show]:
            print("    -", s.render())


if __name__ == "__main__":
    _cli()
