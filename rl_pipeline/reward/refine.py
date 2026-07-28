"""Δbase reward for refine-training groups.

A refine GRPO group shares one prompt: (program + merged rollout pool + verdict
table).  Each sampled refine response is rewarded by how much it raises the
pool's base score (negative-discrimination after real Houdini):

    delta_base[i] = base(Houdini(pool ∪ refined_i)) − base(Houdini(pool))

This IS the refined invariants' marginal contribution to the merged pool.
Under GRPO group normalization the shared base_before is absorbed, so the delta
equals the absolute score gradient-wise — the delta form is kept for monitoring
("how much discrimination the refine recovered").  Δ ≥ 0 always (the pool only
grows, Houdini survivors are monotone), so an all-zero group simply yields no
gradient — expected early in training.

Trivial/copied/weakened refinements get Δ ≈ 0 for free: they must both survive
Houdini AND reject new negatives to score. Each response is capped at 20
invariant lines; ``refine_rewards`` additionally penalizes every overflow line.

The refine prompt and feedback renderer are shared through
``rl_pipeline.common.prompts`` and ``reward.filters`` so training and inference
format the same stateless input.
"""
from __future__ import annotations

from typing import List, Optional

from ..common.state import MAX_INVARIANTS_PER_RESPONSE, dedup_normalized
from ..sampler import ExampleSampler, ExampleSet
from .reward_calculator import RewardCalculator


def refine_group_delta_base(
    source: str,
    pool: List[str],
    refinements: List[List[str]],
    examples: Optional[ExampleSet] = None,
    calculator: Optional[RewardCalculator] = None,
    loop_idx: int = 0,
    max_invariants: int = MAX_INVARIANTS_PER_RESPONSE,
    w_overflow: float = 0.05,
) -> dict:
    """Score one refine group: n refine responses against a shared merged pool.

    ``delta_base`` remains the pure diagnostic. ``refine_rewards`` is the value
    to train on: delta_base minus the response overflow penalty. base_before is
    computed ONCE and shared across the group (n+1 Houdini cascades total; the
    calculator's survive cache dedups overlap).
    """
    if not 1 <= max_invariants <= MAX_INVARIANTS_PER_RESPONSE:
        raise ValueError(
            "max_invariants must be between 1 and "
            f"{MAX_INVARIANTS_PER_RESPONSE}"
        )
    if w_overflow < 0:
        raise ValueError("w_overflow must be non-negative")
    calc = calculator or RewardCalculator(
        w_marg=0.0, w_redundancy=0.0, w_overflow=0.0
    )
    if examples is None:
        examples = ExampleSampler(source, **calc.sampler_kwargs).sample()
    pool = dedup_normalized(pool)
    generated = [len(response) for response in refinements]
    capped = [
        list(response)[:max_invariants] for response in refinements
    ]
    accepted = [len(response) for response in capped]
    overflow = [
        max(0, count - max_invariants) for count in generated
    ]
    merged = [
        dedup_normalized(list(pool) + response) for response in capped
    ]
    # rollout 0 = the un-refined pool; rollouts 1..n = pool ∪ refined_i
    res = calc.compute(
        source,
        [pool] + merged,
        examples=examples,
        loop_idx=loop_idx,
        cap_responses=False,
    )
    base_before = res.rollouts[0].base
    base_after = [r.base for r in res.rollouts[1:]]
    delta_base = [
        max(0.0, after - base_before) for after in base_after
    ]
    overflow_penalty = [w_overflow * count for count in overflow]
    return {
        # Guard the public delta >= 0 contract against floating-point residue.
        "delta_base": delta_base,
        "refine_rewards": [
            delta - penalty
            for delta, penalty in zip(delta_base, overflow_penalty)
        ],
        "generated": generated,
        "accepted": accepted,
        "overflow": overflow,
        "overflow_penalty": overflow_penalty,
        "base_before": base_before,
        "base_after": base_after,
        "pool_size": len(pool),
    }
