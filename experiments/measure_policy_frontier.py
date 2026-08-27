#!/usr/bin/env python3
"""Measure per-program GRPO gradient potential for a policy and keep the frontier.

For each program: sample a group of ``--group`` rollouts from the policy
(vLLM, or ``--mock``), score them with the same ``RewardCalculator`` the
reward service uses, and record the within-group reward statistics.  A
program carries a usable GRPO signal only when the group rewards vary:

    frontier  :=  std(rewards) >= --min-std  and  --min-mean <= mean <= --max-mean

``--apply`` filters a curated parquet down to the frontier (the trainer can
additionally drop zero-variance groups online, DAPO-style; this offline pass
stops paying for programs that are already saturated or hopeless).

Stages are resumable: the ledger is keyed by the sha256 of the visible
program and by policy name.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import digest_of, latest_rows as _latest_rows  # noqa: E402
from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    _atomic_json,
    _display_path,
    _source_from_rl,
)
from rl_pipeline.common import prompts  # noqa: E402
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402
from rl_pipeline.common.state import MAX_INVARIANTS_PER_RESPONSE, extract_invariants  # noqa: E402
from rl_pipeline.reward.filters import PreFramaFilter, auto_filter  # noqa: E402
from rl_pipeline.reward.reward_calculator import RewardCalculator  # noqa: E402
from rl_pipeline.sampler.example_sampler import (  # noqa: E402
    DEFAULT_N_RUNS,
    DEFAULT_SEED,
    NEGATIVE_SCHEMA_VERSION,
)

FRONTIER_SCHEMA_VERSION = 1


def latest_rows(path: Path) -> Dict[str, dict]:
    return _latest_rows(path, key="digest")


# ---------------------------------------------------------------------------
# rollout providers
# ---------------------------------------------------------------------------
class BatchedVLLM:
    """Generate ``n`` rollouts for MANY programs in one vLLM call."""

    def __init__(self, model: str, *, group: int, temperature: float, top_p: float, max_tokens: int,
                 enable_thinking: bool = False, **llm_kwargs):
        from vllm import LLM, SamplingParams
        self.llm = LLM(model=model, **llm_kwargs)
        self.sampling = SamplingParams(n=group, temperature=temperature, top_p=top_p, max_tokens=max_tokens)
        self.chat_kwargs = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
        self.system = prompts.system_prompt()

    def __call__(self, sources: List[str], n: int) -> List[List[List[str]]]:
        sampling = self.sampling
        conversations = [
            [{"role": "system", "content": self.system},
             {"role": "user", "content": prompts.GENERATE_PROMPT.format(program=strip_postcondition(s))}]
            for s in sources
        ]
        try:
            outputs = self.llm.chat(conversations, sampling, use_tqdm=False, **self.chat_kwargs)
        except TypeError:  # older vLLM without chat_template_kwargs
            outputs = self.llm.chat(conversations, sampling, use_tqdm=False)
        return [
            [extract_invariants(o.text, max_invariants=MAX_INVARIANTS_PER_RESPONSE) for o in out.outputs]
            for out in outputs
        ]


class MockBatched:
    """Deterministic stand-in for a policy: random bound/relation clauses over
    the program's loop-head variables, so groups have reward variance."""

    def __init__(self, seed: int = 0):
        self.seed = seed

    def __call__(self, sources: List[str], n: int) -> List[List[List[str]]]:
        import random
        result = []
        for index, source in enumerate(sources):
            rng = random.Random(f"{self.seed}:{index}")
            names = parse_program(strip_postcondition(source)).pre_vars or ["x"]
            pool = [f"{v} >= 0" for v in names] + [f"{v} <= 1000" for v in names]
            pool += [f"{a} <= {b}" for a in names for b in names if a != b]
            pool += [f"{a} == {b}" for a in names for b in names if a < b]
            rollouts = []
            for _ in range(n):
                size = rng.randint(1, min(6, len(pool)))
                rollouts.append(rng.sample(pool, size))
            result.append(rollouts)
        return result


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
_CALC: Optional[RewardCalculator] = None


# The reward the frontier is measured under (RewardCalculator defaults); the
# trainer must POST the same configuration or the verdicts do not transfer.
REWARD_CONFIG = {
    "w_base": 1.0, "w_shapley": 0.3,
    "reward_variant": "full",
}


def _init_scorer(filter_mode: str, n_runs: int, seed: int) -> None:
    global _CALC
    invariant_filter = PreFramaFilter() if filter_mode == "lite" else auto_filter()
    _CALC = RewardCalculator(
        invariant_filter=invariant_filter, n_jobs=1,
        sampler_kwargs={"n_runs": n_runs, "seed": seed},
        **{k: v for k, v in REWARD_CONFIG.items()},
    )


def _score_job(job: Tuple[str, str, List[List[str]]]) -> dict:
    digest, source, rollouts = job
    started = time.perf_counter()
    try:
        assert _CALC is not None
        batch = _CALC.compute(source, rollouts)
        rewards = [r.reward for r in batch.rollouts]
        mean = statistics.fmean(rewards) if rewards else 0.0
        std = statistics.pstdev(rewards) if len(rewards) > 1 else 0.0
        return {
            "digest": digest,
            "status": "ok",
            "scorable": batch.scorable,
            "n_negatives": batch.n_negatives,
            "rewards": [round(r, 4) for r in rewards],
            "base": [round(r.base, 4) for r in batch.rollouts],
            "mean": round(mean, 4),
            "std": round(std, 4),
            "min": round(min(rewards), 4) if rewards else 0.0,
            "max": round(max(rewards), 4) if rewards else 0.0,
            "n_distinct": len({round(r, 4) for r in rewards}),
            "batch_score": round(batch.batch_score, 4),
            "clauses_per_rollout": [len(r) for r in rollouts],
            "seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as error:
        return {"digest": digest, "status": "error",
                "error": f"{type(error).__name__}: {str(error)[:300]}",
                "seconds": round(time.perf_counter() - started, 2)}


def frontier_verdict(row: dict, *, min_std: float, min_mean: float, max_mean: float) -> str:
    if row.get("status") != "ok":
        return "error"
    if not row.get("scorable"):
        return "unscorable"
    if row["std"] < min_std:
        return "saturated" if row["mean"] > max_mean else ("hopeless" if row["mean"] < min_mean else "flat")
    if row["mean"] > max_mean:
        return "saturated"
    if row["mean"] < min_mean:
        return "hopeless"
    return "frontier"


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="curated RL parquet")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--policy", required=True, help="vLLM model path/name, or 'mock'")
    parser.add_argument("--group", type=int, default=8)
    parser.add_argument("--chunk", type=int, default=64, help="programs per vLLM call")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--filter", choices=("auto", "lite"), default="auto",
                        help="auto = PreFrama + Houdini (needs frama-c); lite = sampled-state filter only")
    parser.add_argument("--n-runs", type=int, default=DEFAULT_N_RUNS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="write the frontier parquet + report")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--min-std", type=float, default=0.05)
    parser.add_argument("--min-mean", type=float, default=0.05)
    parser.add_argument("--max-mean", type=float, default=0.95)
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument("--wp-par", type=int, default=2)
    args = parser.parse_args()

    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pq.read_table(args.input)
    records = table.to_pylist()
    programs: List[Tuple[str, str]] = []
    seen = set()
    for record in records:
        source = _source_from_rl(record)
        digest = digest_of(source)
        if digest not in seen:
            seen.add(digest)
            programs.append((digest, source))
    programs = programs[args.offset:]
    if args.limit is not None:
        programs = programs[: args.limit]
    print(f"programs: {len(programs)} policy={args.policy} group={args.group}", flush=True)

    ledger = latest_rows(args.ledger)
    pending = [(d, s) for d, s in programs
               if not (d in ledger and ledger[d].get("policy") == args.policy and ledger[d].get("status") == "ok")]
    if pending:
        provider = MockBatched(args.seed) if args.policy == "mock" else BatchedVLLM(
            args.policy, group=args.group, temperature=args.temperature, top_p=args.top_p,
            max_tokens=args.max_tokens,
        )
        os.environ["CRAFT_WP_TIMEOUT"] = str(args.wp_timeout)
        os.environ["CRAFT_WP_PAR"] = str(args.wp_par)
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        status = Counter()
        with ProcessPoolExecutor(
            max_workers=args.workers, initializer=_init_scorer,
            initargs=(args.filter, args.n_runs, args.seed),
        ) as pool, args.ledger.open("a", encoding="utf-8") as handle:
            for start in range(0, len(pending), args.chunk):
                chunk = pending[start:start + args.chunk]
                rollouts = provider([s for _, s in chunk], args.group)
                futures = {pool.submit(_score_job, (d, s, r)): d for (d, s), r in zip(chunk, rollouts)}
                for future in as_completed(futures):
                    row = future.result()
                    # Frontier verdicts are only meaningful under one reward
                    # configuration; stamp it so drift is detectable.
                    row.update({"policy": args.policy, "group": args.group, "filter": args.filter,
                                "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
                                "reward_config": REWARD_CONFIG})
                    row["verdict"] = frontier_verdict(row, min_std=args.min_std, min_mean=args.min_mean, max_mean=args.max_mean)
                    status[row["verdict"]] += 1
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                    handle.flush()
                print(f"[frontier] {min(start + args.chunk, len(pending))}/{len(pending)} {dict(status)}", flush=True)
        ledger = latest_rows(args.ledger)

    verdicts = Counter()
    keep_digests = set()
    for d, _ in programs:
        row = ledger.get(d)
        verdict = frontier_verdict(row, min_std=args.min_std, min_mean=args.min_mean, max_mean=args.max_mean) if row else "missing"
        verdicts[verdict] += 1
        if verdict == "frontier":
            keep_digests.add(d)
    summary = {
        "schema_version": FRONTIER_SCHEMA_VERSION,
        "policy": args.policy,
        "group": args.group,
        "filter": args.filter,
        "reward_config": REWARD_CONFIG,
        "programs": len(programs),
        "verdicts": dict(verdicts),
        "thresholds": {"min_std": args.min_std, "min_mean": args.min_mean, "max_mean": args.max_mean},
    }
    if args.apply:
        if not args.output or not args.report:
            raise SystemExit("--apply needs --output and --report")
        kept = []
        for record in records:
            if digest_of(_source_from_rl(record)) in keep_digests:
                extra = dict(record.get("extra_info") or {})
                curation = json.loads(extra.get("curation", "{}"))
                row = ledger[digest_of(_source_from_rl(record))]
                curation["frontier"] = {"policy": args.policy, "mean": row["mean"], "std": row["std"]}
                extra["curation"] = json.dumps(curation, sort_keys=True)
                record["extra_info"] = extra
                kept.append(record)
        pq.write_table(pa.Table.from_pylist(kept, schema=table.schema), args.output)
        summary.update({"input": _display_path(args.input), "output": _display_path(args.output),
                        "input_rows": len(records), "output_rows": len(kept)})
        _atomic_json(summary, args.report)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
