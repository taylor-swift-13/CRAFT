"""
RewardCalculator — Component 2.

Given a program and a GROUP of rollouts (each a candidate invariant set), score
each rollout and the batch, using synthetic negative candidates from the sampler:

  whole[A]    = base[A] iff every clause in A survives, else zero
  base[A]     = fraction of negative traces rejected by A's clauses that
                survive pooled Houdini filtering
  shapley[A]  = Shapley allocation of the group's rejection coverage
  reward[A]   = w_base * base[A] + w_shapley * shapley[A]
                - w_overflow * overflow[A]
  batch_score = fraction of candidates rejected by Houdini(union)    (batch performance)

The default credit path first pools all rollout clauses, runs Houdini once, and
then assigns surviving clauses back to every rollout that proposed a
semantically equivalent clause before computing coverage-game Shapley credit.
The historical per-rollout filtering path remains available as ``independent``
for controlled comparisons.

Scoring uses one canonical target-hidden execution sample.  The full source is
used only after Houdini to mine concrete assertion-failing escape traces; it is
never included in the model prompt.  Soundness is delegated to the filter
cascade, which ends in real Houdini (Frama-C/WP).

A candidate set "rejects" a negative valuation s iff some (Houdini-surviving)
invariant evaluates to False at s — a cheap pure-Python check on states.
When the sampler retains no negative traces, coverage variants fall back to
binary inductiveness (1 iff the response is nonempty and every clause survives
Houdini, no penalties), reported as ``scorable=False`` with
``reward_mode="binary_fallback_no_negative_traces"``.  The explicit
``binary`` ablation applies the same rule unconditionally.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Set

from ..common.program import (
    bind_integer_constants,
    parse_program,
    state_external_integer_constants,
    strip_postcondition,
)
from ..common.state import (
    MAX_INVARIANTS_PER_RESPONSE,
    State,
    dedup_normalized,
    eval_predicate,
    extract_invariants,
    invariant_dedup_key,
    normalize_invariant,
)
from ..sampler import ExampleSampler, ExampleSet
from . import filters

REWARD_VARIANTS = (
    "binary",
    "whole_coverage",
    "base",
    "full",
)

CREDIT_FILTER_ORDERS = (
    "pooled",
    "independent",
)

@dataclass
class RolloutScore:
    index: int
    invariants: List[str]
    survivors: List[str]          # surviving clauses attributed to this rollout
    generated: int                # clauses emitted before the response cap
    accepted: int                 # clauses admitted before canonical de-duplication
    overflow: int                 # clauses beyond max_invariants
    base: float
    shapley_credit: float         # allocation of group negative coverage
    overflow_penalty: float
    reward: float
    rejected: int                 # negatives rejected by attributed survivors

@dataclass
class BatchReward:
    program: str
    n_positives: int
    n_negatives: int
    batch_score: float
    filter_mode: str
    rollouts: List[RolloutScore] = field(default_factory=list)
    # Kept after the historical fields for positional-constructor compatibility.
    reward_variant: str = "full"
    negative_sampler: str = "structured"
    credit_filter_order: str = "pooled"

    @property
    def scorable(self) -> bool:
        """True when negative coverage was measurable; False means the group
        was scored by the binary-inductiveness fallback."""
        return self.reward_variant == "binary" or self.n_negatives > 0

    @property
    def reward_mode(self) -> str:
        if self.reward_variant == "binary":
            return "binary_frama_c_validation"
        if not self.scorable:
            return "binary_fallback_no_negative_traces"
        if self.reward_variant == "whole_coverage":
            return "whole_response_negative_coverage"
        return "negative_coverage"

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "n_positives": self.n_positives,
            "n_negatives": self.n_negatives,
            "batch_score": self.batch_score,
            "filter_mode": self.filter_mode,
            "reward_variant": self.reward_variant,
            "negative_sampler": self.negative_sampler,
            "credit_filter_order": self.credit_filter_order,
            "reward_mode": self.reward_mode,
            "scorable": self.scorable,
            "rollout_rewards": [r.reward for r in self.rollouts],
            "base": [r.base for r in self.rollouts],
            "shapley_credit": [r.shapley_credit for r in self.rollouts],
            "overflow_penalty": [r.overflow_penalty for r in self.rollouts],
            "generated": [r.generated for r in self.rollouts],
            "accepted": [r.accepted for r in self.rollouts],
            "overflow": [r.overflow for r in self.rollouts],
            "rollouts": [
                {"index": r.index, "reward": r.reward, "base": r.base,
                 "shapley_credit": r.shapley_credit,
                 "overflow_penalty": r.overflow_penalty,
                 "generated": r.generated, "accepted": r.accepted,
                 "overflow": r.overflow,
                 "rejected": r.rejected,
                 "survivors": r.survivors}
                for r in self.rollouts
            ],
        }


def _rollout_invariants_raw(rollout) -> List[str]:
    """Accept {'invariants': [...]} or {'code': '<annotated>'} or a raw list/str.

    A string may be (a) a JSON-encoded dict/list (unwrapped and recursed), or
    (b) raw LLM text / annotated code containing `loop invariant ...;` lines.
    """
    if isinstance(rollout, dict):
        if rollout.get("invariants"):
            invs = rollout["invariants"]
        elif rollout.get("code"):
            invs = extract_invariants(rollout["code"])
        else:
            invs = []
    elif isinstance(rollout, (list, tuple)):
        invs = list(rollout)
    elif isinstance(rollout, str):
        s = rollout.strip()
        parsed = None
        if s[:1] in ("{", "["):
            try:
                parsed = json.loads(s)
            except ValueError:
                parsed = None
        if parsed is not None:
            return _rollout_invariants_raw(parsed)
        # raw text / annotated code: extract explicit `loop invariant` lines only
        invs = extract_invariants(s)
    else:
        invs = []
    if isinstance(invs, str):
        extracted = extract_invariants(invs)
        invs = extracted or [invs]
    return [normalized for inv in invs
            if (normalized := normalize_invariant(inv))]


def _rollout_invariants(rollout) -> List[str]:
    """Canonical, de-duplicated invariants used by the filter cascade."""
    return dedup_normalized(_rollout_invariants_raw(rollout))


class RewardCalculator:
    def __init__(
        self,
        invariant_filter=None,
        w_base: float = 1.0,
        w_shapley: float = 0.3,
        w_overflow: float = 0.1,
        max_invariants: int = MAX_INVARIANTS_PER_RESPONSE,
        n_jobs: Optional[int] = None,     # parallel frama-c filter calls per group
        logger: Optional[logging.Logger] = None,
        sampler_kwargs: Optional[dict] = None,
        reward_variant: str = "full",
        credit_filter_order: str = "pooled",
    ):
        if reward_variant not in REWARD_VARIANTS:
            raise ValueError(
                "reward_variant must be one of: "
                + ", ".join(REWARD_VARIANTS)
            )
        if credit_filter_order not in CREDIT_FILTER_ORDERS:
            raise ValueError(
                "credit_filter_order must be one of: "
                + ", ".join(CREDIT_FILTER_ORDERS)
            )
        log = logger or logging.getLogger("rl_pipeline.reward")
        self.filter = invariant_filter or filters.auto_filter(log)
        self.w_base = w_base
        self.w_shapley = w_shapley
        self.w_overflow = w_overflow
        self.max_invariants = max_invariants
        self.n_jobs = n_jobs or min(16, (os.cpu_count() or 8))
        self.sampler_kwargs = sampler_kwargs or {}
        self.reward_variant = reward_variant
        self.credit_filter_order = credit_filter_order
        if min(self.w_base, self.w_shapley, self.w_overflow) < 0:
            raise ValueError("reward weights must be non-negative")
        if not 1 <= self.max_invariants <= MAX_INVARIANTS_PER_RESPONSE:
            raise ValueError(
                "max_invariants must be between 1 and "
                f"{MAX_INVARIANTS_PER_RESPONSE}"
            )

    # ── negative-rejection bookkeeping ───────────────────────────────────────
    @staticmethod
    def _rejected_set(
        negatives: List[State],
        invariants: List[str],
        constants: Optional[dict[str, int]] = None,
    ) -> Set[int]:
        """Indices of negatives excluded by at least one invariant."""
        rej: Set[int] = set()
        for inv in invariants:
            cond = bind_integer_constants(
                normalize_invariant(inv), constants or {}
            )
            for i, s in enumerate(negatives):
                if i in rej:
                    continue
                if eval_predicate(cond, s) is False:
                    rej.add(i)
        return rej

    # ── main ─────────────────────────────────────────────────────────────────
    def compute(self, source: str, rollouts: List,
                examples: Optional[ExampleSet] = None,
                loop_idx: int = 0,
                cap_responses: bool = True) -> BatchReward:
        """Score rollouts without exposing the held-out target to the reward path."""
        # Trainers may submit the original benchmark record so that one payload
        # can also be archived for final evaluation.  Neither trace sampling nor
        # inductiveness filtering needs its assertion/ensures clause, so enforce
        # the target-hidden boundary here rather than relying on every caller.
        source = strip_postcondition(source)
        if examples is None:
            examples = ExampleSampler(source, **self.sampler_kwargs).sample()
        return self._compute_one(
            source, rollouts, examples, loop_idx, cap_responses
        )

    @staticmethod
    def _to_groups(state_rej: Set[int], groups: List[List[int]]) -> Set[int]:
        """Candidate-trace indices rejected when any witness state is rejected."""
        return {g for g, idxs in enumerate(groups) if any(i in state_rej for i in idxs)}

    def _compute_one(self, source: str, rollouts: List, examples: ExampleSet,
                     loop_idx: int = 0,
                     cap_responses: bool = True) -> BatchReward:
        prog = parse_program(source)
        constants = state_external_integer_constants(prog)
        if not 0 <= loop_idx < len(prog.loops):
            raise ValueError(
                f"loop_idx {loop_idx} is out of range for {len(prog.loops)} loops"
            )
        positives = examples.pos(loop_idx)
        negatives = examples.neg(loop_idx)
        # Scoring unit = synthetic trace candidate (witness group), not state.
        if hasattr(examples, "groups"):
            groups = examples.groups(loop_idx)
        else:
            groups = [[i] for i in range(len(negatives))]
        n_neg = len(groups)
        scorable = self.reward_variant == "binary" or n_neg > 0

        # Keep the model's actual clause count for generated/accepted/overflow
        # accounting. Filtering/scoring uses a canonical
        # de-duplicated set of the first ``max_invariants`` clauses.
        roll_invs_raw = [_rollout_invariants_raw(r) for r in rollouts]
        roll_invs_capped = [
            invs[:self.max_invariants] if cap_responses else invs
            for invs in roll_invs_raw
        ]
        roll_invs = [dedup_normalized(invs) for invs in roll_invs_capped]

        # Memoize filter results across the clause sets required by the chosen
        # credit path.  The default pooled path needs exactly one nonempty
        # Houdini call for the group.
        survive_cache: dict = {}

        def survive(invs: List[str]) -> List[str]:
            if not invs:
                return []
            # Frama-C may introduce invariant proof dependencies in annotation
            # order.  Preserve the pooled first-occurrence order used by
            # inference, and include that order in the memoization key.
            key = tuple(normalize_invariant(i) for i in invs)
            cached = survive_cache.get(key)
            if cached is None:
                cached = self.filter.filter(prog, loop_idx, invs, positives)
                survive_cache[key] = cached
            return cached

        # PRE-WARM the survive cache in parallel for the historical independent
        # path.  Pooled attribution intentionally filters only the union.
        union = dedup_normalized(c for invs in roll_invs for c in invs)
        needed = (
            [union]
            if self.credit_filter_order == "pooled"
            else [union] + roll_invs
        )
        uniq = {tuple(normalize_invariant(i) for i in invs): invs
                for invs in needed if invs}
        if len(uniq) > 1 and self.n_jobs > 1:
            with ThreadPoolExecutor(max_workers=self.n_jobs) as ex:
                list(ex.map(lambda kv: survive_cache.__setitem__(
                    kv[0], self.filter.filter(prog, loop_idx, kv[1], positives)),
                    uniq.items()))
        union_surv = survive(union)
        if self.credit_filter_order == "pooled":
            # Preserve provenance after union de-duplication.  Semantic keys
            # ensure aliases such as ``x >= 0`` and ``0 <= x`` share the same
            # pooled survivor while retaining each rollout as an owner.
            surviving_keys = {
                invariant_dedup_key(inv) for inv in union_surv
            }
            rollout_survivors = [
                [
                    inv for inv in invs
                    if invariant_dedup_key(inv) in surviving_keys
                ]
                for invs in roll_invs
            ]
        else:
            rollout_survivors = [survive(invs) for invs in roll_invs]
        rollout_base_rejections = [
            self._to_groups(
                self._rejected_set(negatives, survivors, constants), groups
            )
            for survivors in rollout_survivors
        ]
        group_reject_count = [
            sum(group in rejected for rejected in rollout_base_rejections)
            for group in range(n_neg)
        ]
        # Closed-form Shapley allocation for the negative-set coverage game.
        # A trace rejected by f rollouts contributes 1/f to each of them, so
        # the per-rollout credits sum exactly to attributed union coverage.
        group_shapley_weight = [
            1.0 / count if count else 0.0
            for count in group_reject_count
        ]
        union_rej = self._to_groups(
            self._rejected_set(negatives, union_surv, constants), groups
        )
        def fully_verified(
            candidates: List[str], survivors: List[str]
        ) -> bool:
            candidate_set = frozenset(
                normalize_invariant(i) for i in candidates
            )
            survivor_set = frozenset(
                normalize_invariant(i) for i in survivors
            )
            return bool(candidate_set) and survivor_set == candidate_set

        if not scorable or self.reward_variant == "binary":
            batch_score = 1.0 if fully_verified(union, union_surv) else 0.0
        elif self.reward_variant == "whole_coverage":
            batch_score = (
                len(union_rej) / n_neg
                if fully_verified(union, union_surv) else 0.0
            )
        else:
            batch_score = len(union_rej) / n_neg

        scores: List[RolloutScore] = []
        for idx, invs in enumerate(roll_invs):
            # base: attributed Houdini survivors, counted in trace units
            surv = rollout_survivors[idx]
            base_rej = rollout_base_rejections[idx]
            base = (len(base_rej) / n_neg) if n_neg else 0.0
            shapley_credit = (
                sum(group_shapley_weight[group] for group in base_rej) / n_neg
                if n_neg else 0.0
            )
            n_generated = len(roll_invs_raw[idx])
            n_accepted = len(roll_invs_capped[idx])
            overflow = (
                max(0, n_generated - self.max_invariants)
                if cap_responses else 0
            )
            overflow_penalty = self.w_overflow * overflow
            if not scorable or self.reward_variant == "binary":
                # Binary inductiveness: either the explicit ablation or the
                # fallback when no negative trace exists to measure strength.
                overflow_penalty = 0.0
                reward = 1.0 if fully_verified(invs, surv) else 0.0
            elif self.reward_variant == "whole_coverage":
                # All-or-nothing response control: use the same dense
                # negative-coverage signal as ``base``, but do not salvage a
                # sound subset from an imperfect response.
                reward = (
                    (base if fully_verified(invs, surv) else 0.0)
                    - overflow_penalty
                )
            else:
                reward = self.w_base * base
                if self.reward_variant == "full":
                    reward += self.w_shapley * shapley_credit
                reward -= overflow_penalty
            scores.append(RolloutScore(
                index=idx, invariants=invs, survivors=surv,
                generated=n_generated, accepted=n_accepted, overflow=overflow,
                base=base, shapley_credit=shapley_credit,
                overflow_penalty=overflow_penalty,
                reward=reward, rejected=len(base_rej),
            ))

        return BatchReward(
            program=prog.func_name,
            n_positives=len(positives),
            n_negatives=n_neg,
            batch_score=batch_score,
            filter_mode=getattr(self.filter, "name", "unknown"),
            reward_variant=self.reward_variant,
            negative_sampler=getattr(
                examples, "negative_sampler", "structured"
            ),
            credit_filter_order=self.credit_filter_order,
            rollouts=scores,
        )
