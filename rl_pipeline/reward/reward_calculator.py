"""
RewardCalculator — Component 2.

Given a program and a GROUP of rollouts (each a candidate invariant set), score
each rollout and the batch, using synthetic negative candidates from the sampler:

  base[A]     = fraction of candidates rejected by Houdini(A alone)
  marginal[A] = fraction of candidates that Houdini(union) rejects but
                Houdini(union \ A) does not                          (ablation 增益)
  reward[A]   = w_base * base[A] + w_marg * marginal[A]
                + 0.3 * min(essential[A] / 8, 1)
  batch_score = fraction of candidates rejected by Houdini(union)    (batch performance)
  should_reroll = batch_score < reroll_threshold

Scoring uses ONE canonical example set; soundness is delegated entirely to the
filter cascade, which ends in real Houdini (Frama-C/WP).

A candidate set "rejects" a negative valuation s iff some (Houdini-surviving)
invariant evaluates to False at s — a cheap pure-Python check on states.  The
only Frama-C cost is the Houdini step (swappable; see filters.py).
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
    State, eval_predicate, normalize_invariant, dedup_normalized, extract_invariants,
)
from ..sampler import ExampleSampler, ExampleSet
from . import filters

@dataclass
class RolloutScore:
    index: int
    invariants: List[str]
    survivors: List[str]          # Houdini-surviving invariants (standalone)
    base: float
    marginal: float
    precision: float              # essential survivors / generated clauses
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

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "n_positives": self.n_positives,
            "n_negatives": self.n_negatives,
            "batch_score": self.batch_score,
            "should_reroll": self.should_reroll,
            "filter_mode": self.filter_mode,
            "rollout_rewards": [r.reward for r in self.rollouts],
            "base": [r.base for r in self.rollouts],
            "marginal": [r.marginal for r in self.rollouts],
            "precision": [r.precision for r in self.rollouts],
            "rollouts": [
                {"index": r.index, "reward": r.reward, "base": r.base,
                 "marginal": r.marginal, "precision": r.precision,
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
        w_base: float = 0.8,
        w_marg: float = 0.2,
        reroll_threshold: float = 0.6,
        n_jobs: Optional[int] = None,     # parallel frama-c filter calls per group
        logger: Optional[logging.Logger] = None,
        sampler_kwargs: Optional[dict] = None,
    ):
        log = logger or logging.getLogger("rl_pipeline.reward")
        self.filter = invariant_filter or filters.auto_filter(log)
        self.w_base = w_base
        self.w_marg = w_marg
        self.reroll_threshold = reroll_threshold
        self.n_jobs = n_jobs or min(16, (os.cpu_count() or 8))
        self.sampler_kwargs = sampler_kwargs or {}
        if self.w_base < 0 or self.w_marg < 0:
            raise ValueError("reward weights must be non-negative")
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
                loop_idx: int = 0) -> BatchReward:
        """Score rollouts against one example set (sampled canonically if omitted)."""
        if examples is None:
            examples = ExampleSampler(source, **self.sampler_kwargs).sample()
        return self._compute_one(source, rollouts, examples, loop_idx)

    @staticmethod
    def _to_groups(state_rej: Set[int], groups: List[List[int]]) -> Set[int]:
        """Candidate-trace indices rejected when any witness state is rejected."""
        return {g for g, idxs in enumerate(groups) if any(i in state_rej for i in idxs)}

    @staticmethod
    def _essential_count(
        survivors: List[str], negatives: List[State], groups: List[List[int]],
    ) -> int:
        """Count survivors that greedily extend negative-trace coverage.

        Equivalent, weaker, duplicate, and tautological clauses add no new
        rejected group after an earlier clause and therefore are not essential.
        """
        covered: Set[int] = set()
        essential = 0
        for invariant in survivors:
            rejected_states = RewardCalculator._rejected_set(
                negatives, [invariant]
            )
            rejected_groups = RewardCalculator._to_groups(
                rejected_states, groups
            )
            if rejected_groups - covered:
                covered |= rejected_groups
                essential += 1
        return essential

    def _compute_one(self, source: str, rollouts: List, examples: ExampleSet,
                     loop_idx: int = 0) -> BatchReward:
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

        # Preserve the model's actual clause count for precision monitoring.
        # Filtering/scoring still uses a canonical de-duplicated set.
        roll_invs_raw = [_rollout_invariants_raw(r) for r in rollouts]
        roll_invs = [dedup_normalized(invs) for invs in roll_invs_raw]

        # memoize filter results across base/union/ablation calls — the ablation
        # subsets (∪ \ A) overlap heavily, so identical invariant sets are filtered
        # (and, in Houdini mode, verified by Frama-C) only once.
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
        # scoring below needs (per-rollout, union, ablation rests) is filtered
        # concurrently — frama-c runs are independent subprocesses.
        union = dedup_normalized(c for invs in roll_invs for c in invs)
        needed = [union] + roll_invs
        if self.w_marg:
            needed += [dedup_normalized(c for j, other in enumerate(roll_invs) if j != idx for c in other)
                       for idx in range(len(roll_invs))]
        uniq = {frozenset(normalize_invariant(i) for i in invs): invs
                for invs in needed if invs}
        if len(uniq) > 1 and self.n_jobs > 1:
            with ThreadPoolExecutor(max_workers=self.n_jobs) as ex:
                list(ex.map(lambda kv: survive_cache.__setitem__(
                    kv[0], self.filter.filter(prog, loop_idx, sorted(kv[0]), positives)),
                    uniq.items()))
        union_surv = survive(union)
        union_rej = self._to_groups(self._rejected_set(negatives, union_surv), groups)
        if n_neg:
            batch_score = len(union_rej) / n_neg
        else:
            batch_score = (len(union_surv) / len(union)) if union else 0.0

        scores: List[RolloutScore] = []
        for idx, invs in enumerate(roll_invs):
            # base: standalone Houdini survivors, counted in trace units
            surv = survive(invs)
            base_rej = self._to_groups(self._rejected_set(negatives, surv), groups)
            base = (len(base_rej) / n_neg) if n_neg else 0.0
            # marginal: ablation on the union (skipped when unweighted — the
            # ablation refilters |rollouts| near-union sets, dominating cost)
            if self.w_marg:
                rest = dedup_normalized(c for j, other in enumerate(roll_invs) if j != idx for c in other)
                rest_surv = survive(rest)
                rest_rej = self._to_groups(self._rejected_set(negatives, rest_surv), groups)
                marginal = (len(union_rej - rest_rej) / n_neg) if n_neg else 0.0
            else:
                marginal = 0.0
            if n_neg:
                raw_reward = self.w_base * base + self.w_marg * marginal
                n_generated = len(roll_invs_raw[idx])
                n_essential = self._essential_count(surv, negatives, groups)
                precision = (
                    n_essential / n_generated if n_generated else 0.0
                )
                essential_bonus = min(n_essential / 8.0, 1.0)
                reward = raw_reward + 0.3 * essential_bonus
            else:
                # No finite negative set is available: retain a bounded Houdini
                # signal instead of returning an all-zero GRPO group.
                precision = 1.0
                reward = (len(surv) / len(invs)) if invs else 0.0
            scores.append(RolloutScore(
                index=idx, invariants=invs, survivors=surv,
                base=base, marginal=marginal, precision=precision,
                reward=reward, rejected=len(base_rej),
            ))

        return BatchReward(
            program=prog.func_name,
            n_positives=len(positives),
            n_negatives=n_neg,
            batch_score=batch_score,
            should_reroll=batch_score < self.reroll_threshold,
            filter_mode=getattr(self.filter, "name", "unknown"),
            rollouts=scores,
        )
