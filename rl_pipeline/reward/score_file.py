"""
Batch-score a JSONL/Parquet file of rollout groups -> reward rows.

    python -m rl_pipeline.reward.score_file --input rollouts.parquet --output rewards.parquet
    python -m rl_pipeline.reward.score_file --input rollouts.jsonl   --output rewards.jsonl

Reads rollout batches (see io.py for layouts), computes per-rollout rewards with
RewardCalculator (Houdini-lite -> real Houdini cascade when frama-c is present),
and writes one reward row per rollout.  Example sets are cached per program.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
from typing import Dict, Optional

from ..sampler import ExampleSampler, ExampleSet, NEGATIVE_SAMPLER_MODES
from ..sampler.example_sampler import DEFAULT_N_RUNS, DEFAULT_SEED
from . import filters, io
from .reward_calculator import (
    CREDIT_FILTER_ORDERS,
    REWARD_VARIANTS,
    RewardCalculator,
)


def score_file(input_path: str, output_path: str, cfg: io.IOConfig,
               sampler_kwargs: Optional[dict] = None,
               w_base: float = 1.0,
               include_program: bool = False,
               logger: Optional[logging.Logger] = None,
               w_shapley: float = 0.3,
               w_overflow: float = 0.1,
               reward_variant: str = "full",
               credit_filter_order: str = "pooled") -> Dict[str, int]:
    sampler_kwargs = dict(sampler_kwargs or {})
    logger = logger or logging.getLogger("rl_pipeline.reward.score_file")
    batches = io.read_batches(input_path, cfg)
    logger.info("read %d batches from %s", len(batches), input_path)

    shared_filter = filters.auto_filter(logger)
    rc = RewardCalculator(invariant_filter=shared_filter, w_base=w_base,
                          w_shapley=w_shapley,
                          w_overflow=w_overflow,
                          reward_variant=reward_variant,
                          credit_filter_order=credit_filter_order,
                          logger=logger)

    example_cache: Dict[str, ExampleSet] = {}

    def get_examples(program: str) -> ExampleSet:
        key = hashlib.sha1((program + repr(sorted(sampler_kwargs.items()))).encode()).hexdigest()
        es = example_cache.get(key)
        if es is None:
            es = ExampleSampler(program, **sampler_kwargs).sample()
            example_cache[key] = es
        return es

    out_rows = []
    n_failed = 0
    for bi, batch in enumerate(batches):
        try:
            examples = get_examples(batch.program)
            br = rc.compute(batch.program, batch.rollouts, examples=examples)
        except Exception as e:
            logger.warning("batch %s failed: %s", batch.group_id, e)
            n_failed += 1
            continue
        out_rows.extend(io.batch_reward_to_rows(batch, br, include_program=include_program))
        logger.info("batch %d/%d (%s): batch_score=%.3f rewards=%s",
                    bi + 1, len(batches), batch.group_id, br.batch_score,
                    [round(r.reward, 3) for r in br.rollouts])

    io.write_rows(output_path, out_rows)
    stats = {
        "batches": len(batches),
        "failed": n_failed,
        "rows": len(out_rows),
        "programs": len(example_cache),
    }
    logger.info("wrote %d reward rows -> %s  (%s)", len(out_rows), output_path, stats)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-score rollout groups from JSONL/Parquet")
    ap.add_argument("--input", required=True, help="rollouts .jsonl or .parquet")
    ap.add_argument("--output", required=True, help="rewards .jsonl or .parquet")
    ap.add_argument("--program-field", default="program")
    ap.add_argument("--rollouts-field", default="rollouts")
    ap.add_argument("--response-field", default="response")
    ap.add_argument("--group-field", default="group_id")
    ap.add_argument("--w-base", type=float, default=1.0)
    ap.add_argument("--w-shapley", type=float, default=0.3)
    ap.add_argument("--w-overflow", type=float, default=0.1)
    ap.add_argument(
        "--reward-variant", choices=REWARD_VARIANTS, default="full"
    )
    ap.add_argument(
        "--credit-filter-order",
        choices=CREDIT_FILTER_ORDERS,
        default="pooled",
        help="filter the pooled union once (default) or each rollout separately",
    )
    ap.add_argument("--include-program", action="store_true", help="keep program column in output")
    # sampler knobs
    ap.add_argument("--runs", type=int, default=DEFAULT_N_RUNS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument(
        "--negative-sampler",
        choices=NEGATIVE_SAMPLER_MODES,
        default="structured",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("rl_pipeline.reward.score_file")

    cfg = io.IOConfig(program_field=args.program_field, rollouts_field=args.rollouts_field,
                      response_field=args.response_field, group_field=args.group_field)
    sampler_kwargs = {
        "n_runs": args.runs,
        "seed": args.seed,
        "negative_sampler": args.negative_sampler,
    }
    stats = score_file(
        args.input, args.output, cfg, sampler_kwargs,
        args.w_base, args.include_program, logger,
        w_shapley=args.w_shapley,
        w_overflow=args.w_overflow,
        reward_variant=args.reward_variant,
        credit_filter_order=args.credit_filter_order,
    )
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
