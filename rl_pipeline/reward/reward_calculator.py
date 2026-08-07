"""
RewardCalculator — Component 2.

Given a program and a GROUP of rollouts (each a candidate invariant set), score
each rollout and the batch, using synthetic negative candidates from the sampler:

  whole[A]    = base[A] iff every clause in A survives, else zero
  base[A]     = fraction of candidates rejected by Houdini(A alone)
  shapley[A]  = Shapley allocation of the group's rejection coverage
  reward[A]   = w_base * base[A] + w_shapley * shapley[A]
                - redundancy_penalty[A] - overflow_penalty[A]
  batch_score = fraction of candidates rejected by Houdini(union)    (batch performance)
  should_reroll = batch_score < reroll_threshold

Scoring uses ONE canonical example set; soundness is delegated entirely to the
filter cascade, which ends in real Houdini (Frama-C/WP).

A candidate set "rejects" a negative valuation s iff some (Houdini-surviving)
invariant evaluates to False at s — a cheap pure-Python check on states.  When
no safe negatives exist, the fallback is binary: one iff every candidate in a
non-empty rollout survives Frama-C/WP validation, otherwise zero.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Set

from ..common.program import parse_program
from ..common.state import (
    MAX_INVARIANTS_PER_RESPONSE,
    State,
    dedup_normalized,
    eval_predicate,
    extract_invariants,
    normalize_invariant,
)
from ..sampler import ExampleSampler, ExampleSet
from . import filters

REWARD_VARIANTS = (
    "binary",
    "whole_coverage",
    "base",
    "base_shapley",
    "full",
)


@dataclass
class RolloutScore:
    index: int
    invariants: List[str]
    survivors: List[str]          # Houdini-surviving invariants (standalone)
    generated: int                # clauses emitted before the response cap
    accepted: int                 # clauses admitted before canonical de-duplication
    overflow: int                 # clauses beyond max_invariants
    base: float
    shapley_credit: float         # allocation of group negative coverage
    redundant_clauses: int        # conservative semantic duplicate emissions
    redundancy_penalty: float
    overflow_penalty: float
    reward: float
    rejected: int                 # negatives rejected standalone

@dataclass
class BatchReward:
    program: str
    n_positives: int
    n_negatives: int
    batch_score: float
    should_reroll: bool
    filter_mode: str
    rollouts: List[RolloutScore] = field(default_factory=list)
    # Kept after the historical fields for positional-constructor compatibility.
    reward_variant: str = "full"
    negative_sampler: str = "structured"

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "n_positives": self.n_positives,
            "n_negatives": self.n_negatives,
            "batch_score": self.batch_score,
            "should_reroll": self.should_reroll,
            "filter_mode": self.filter_mode,
            "reward_variant": self.reward_variant,
            "negative_sampler": self.negative_sampler,
            "reward_mode": (
                "binary_frama_c_validation"
                if self.reward_variant == "binary" or not self.n_negatives
                else (
                    "whole_response_negative_coverage"
                    if self.reward_variant == "whole_coverage"
                    else "negative_coverage"
                )
            ),
            "rollout_rewards": [r.reward for r in self.rollouts],
            "base": [r.base for r in self.rollouts],
            "shapley_credit": [r.shapley_credit for r in self.rollouts],
            "redundant_clauses": [
                r.redundant_clauses for r in self.rollouts
            ],
            "redundancy_penalty": [
                r.redundancy_penalty for r in self.rollouts
            ],
            "overflow_penalty": [r.overflow_penalty for r in self.rollouts],
            "generated": [r.generated for r in self.rollouts],
            "accepted": [r.accepted for r in self.rollouts],
            "overflow": [r.overflow for r in self.rollouts],
            "rollouts": [
                {"index": r.index, "reward": r.reward, "base": r.base,
                 "shapley_credit": r.shapley_credit,
                 "redundant_clauses": r.redundant_clauses,
                 "redundancy_penalty": r.redundancy_penalty,
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
        w_redundancy: float = 0.02,
        w_overflow: float = 0.05,
        max_invariants: int = MAX_INVARIANTS_PER_RESPONSE,
        reroll_threshold: float = 0.6,
        n_jobs: Optional[int] = None,     # parallel frama-c filter calls per group
        logger: Optional[logging.Logger] = None,
        sampler_kwargs: Optional[dict] = None,
        reward_variant: str = "full",
    ):
        if reward_variant not in REWARD_VARIANTS:
            raise ValueError(
                "reward_variant must be one of: "
                + ", ".join(REWARD_VARIANTS)
            )
        log = logger or logging.getLogger("rl_pipeline.reward")
        self.filter = invariant_filter or filters.auto_filter(log)
        self.w_base = w_base
        self.w_shapley = w_shapley
        self.w_redundancy = w_redundancy
        self.w_overflow = w_overflow
        self.max_invariants = max_invariants
        self.reroll_threshold = reroll_threshold
        self.n_jobs = n_jobs or min(16, (os.cpu_count() or 8))
        self.sampler_kwargs = sampler_kwargs or {}
        self.reward_variant = reward_variant
        if min(
            self.w_base,
            self.w_shapley,
            self.w_redundancy,
            self.w_overflow,
        ) < 0:
            raise ValueError("reward weights must be non-negative")
        if not 1 <= self.max_invariants <= MAX_INVARIANTS_PER_RESPONSE:
            raise ValueError(
                "max_invariants must be between 1 and "
                f"{MAX_INVARIANTS_PER_RESPONSE}"
            )
        if not 0.0 <= self.reroll_threshold <= 1.0:
            raise ValueError("reroll_threshold must be between 0 and 1")

    # ── negative-rejection bookkeeping ───────────────────────────────────────
    @staticmethod
    def _rejected_set(negatives: List[State], invariants: List[str]) -> Set[int]:
        """Indices of negatives excluded by at least one invariant."""
        rej: Set[int] = set()
        for inv in invariants:
            cond = normalize_invariant(inv)
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
        """Score rollouts against one example set (sampled canonically if omitted)."""
        if examples is None:
            examples = ExampleSampler(source, **self.sampler_kwargs).sample()
        return self._compute_one(
            source, rollouts, examples, loop_idx, cap_responses
        )

    @staticmethod
    def _to_groups(state_rej: Set[int], groups: List[List[int]]) -> Set[int]:
        """Candidate-trace indices rejected when any witness state is rejected."""
        return {g for g, idxs in enumerate(groups) if any(i in state_rej for i in idxs)}

    @staticmethod
    def _duplicate_clause_count(clauses: List[str]) -> int:
        """Count conservative semantic duplicates, independent of output order.

        A repeated clause disappears under the canonical set semantics used by
        both training and inference, so removing the repeated occurrence cannot
        affect Houdini, negative coverage, or a held-out proof target.  Unique
        zero-coverage clauses are deliberately *not* treated as redundant: they
        may support another clause's inductiveness or a target direction that
        the sampled negatives do not exercise.
        """
        normalized = [normalized for clause in clauses
                      if (normalized := normalize_invariant(clause))]
        return len(normalized) - len(dedup_normalized(normalized))

    def _compute_one(self, source: str, rollouts: List, examples: ExampleSet,
                     loop_idx: int = 0,
                     cap_responses: bool = True) -> BatchReward:
        prog = parse_program(source)
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

        # Preserve the model's actual clause count for overflow and duplicate
        # accounting. Filtering/scoring uses a canonical de-duplicated set.
        roll_invs_raw = [_rollout_invariants_raw(r) for r in rollouts]
        roll_invs_capped = [
            invs[:self.max_invariants] if cap_responses else invs
            for invs in roll_invs_raw
        ]
        roll_invs = [dedup_normalized(invs) for invs in roll_invs_capped]

        # Memoize filter results across per-rollout and union calls.
        survive_cache: dict = {}

        def survive(invs: List[str]) -> List[str]:
            if not invs:
                return []
            key = frozenset(normalize_invariant(i) for i in invs)
            cached = survive_cache.get(key)
            if cached is None:
                cached = self.filter.filter(prog, loop_idx, sorted(key), positives)
                survive_cache[key] = cached
            return cached

        # PRE-WARM the survive cache in parallel: every distinct clause set the
        # scoring below needs is filtered concurrently.
        union = dedup_normalized(c for invs in roll_invs for c in invs)
        needed = [union] + roll_invs
        uniq = {frozenset(normalize_invariant(i) for i in invs): invs
                for invs in needed if invs}
        if len(uniq) > 1 and self.n_jobs > 1:
            with ThreadPoolExecutor(max_workers=self.n_jobs) as ex:
                list(ex.map(lambda kv: survive_cache.__setitem__(
                    kv[0], self.filter.filter(prog, loop_idx, sorted(kv[0]), positives)),
                    uniq.items()))
        union_surv = survive(union)
        rollout_survivors = [survive(invs) for invs in roll_invs]
        rollout_base_rejections = [
            self._to_groups(
                self._rejected_set(negatives, survivors), groups
            )
            for survivors in rollout_survivors
        ]
        group_reject_count = [
            sum(group in rejected for rejected in rollout_base_rejections)
            for group in range(n_neg)
        ]
        # Closed-form Shapley allocation for the negative-set coverage game.
        # A trace rejected by f rollouts contributes 1/f to each of them, so
        # the per-rollout credits sum exactly to standalone union coverage.
        group_shapley_weight = [
            1.0 / count if count else 0.0
            for count in group_reject_count
        ]
        union_rej = self._to_groups(self._rejected_set(negatives, union_surv), groups)
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

        if self.reward_variant == "binary":
            batch_score = 1.0 if fully_verified(union, union_surv) else 0.0
        elif self.reward_variant == "whole_coverage" and n_neg:
            batch_score = (
                len(union_rej) / n_neg
                if fully_verified(union, union_surv) else 0.0
            )
        elif n_neg:
            batch_score = len(union_rej) / n_neg
        else:
            batch_score = (
                1.0 if fully_verified(union, union_surv) else 0.0
            )

        scores: List[RolloutScore] = []
        for idx, invs in enumerate(roll_invs):
            # base: standalone Houdini survivors, counted in trace units
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
            if self.reward_variant == "binary":
                redundancy_penalty = 0.0
                redundant_clauses = 0
                overflow_penalty = 0.0
                reward = 1.0 if fully_verified(invs, surv) else 0.0
            elif self.reward_variant == "whole_coverage" and n_neg:
                # All-or-nothing response control: use the same dense
                # negative-coverage signal as ``base``, but do not salvage a
                # sound subset from an imperfect response. Keep the overflow
                # cost shared by all coverage variants so the comparison with
                # ``base`` isolates clause-subset credit.
                redundancy_penalty = 0.0
                redundant_clauses = 0
                reward = (
                    (base if fully_verified(invs, surv) else 0.0)
                    - overflow_penalty
                )
            elif n_neg:
                redundant_clauses = self._duplicate_clause_count(
                    roll_invs_capped[idx]
                )
                raw_reward = self.w_base * base
                if self.reward_variant in ("base_shapley", "full"):
                    raw_reward += self.w_shapley * shapley_credit
                redundancy_penalty = (
                    self.w_redundancy * redundant_clauses
                    if self.reward_variant == "full" else 0.0
                )
                reward = (
                    raw_reward
                    - redundancy_penalty
                    - overflow_penalty
                )
            else:
                # Pure binary fallback: every candidate in this non-empty
                # rollout must survive Frama-C/WP validation. Do not mix
                # structural penalties into its public {0, 1} contract.
                redundancy_penalty = 0.0
                redundant_clauses = 0
                overflow_penalty = 0.0
                reward = 1.0 if fully_verified(invs, surv) else 0.0
            scores.append(RolloutScore(
                index=idx, invariants=invs, survivors=surv,
                generated=n_generated, accepted=n_accepted, overflow=overflow,
                base=base, shapley_credit=shapley_credit,
                redundant_clauses=redundant_clauses,
                redundancy_penalty=redundancy_penalty,
                overflow_penalty=overflow_penalty,
                reward=reward, rejected=len(base_rej),
            ))

        return BatchReward(
            program=prog.func_name,
            n_positives=len(positives),
            n_negatives=n_neg,
            batch_score=batch_score,
            should_reroll=batch_score < self.reroll_threshold,
            filter_mode=getattr(self.filter, "name", "unknown"),
            reward_variant=self.reward_variant,
            negative_sampler=getattr(
                examples, "negative_sampler", "structured"
            ),
            rollouts=scores,
        )
