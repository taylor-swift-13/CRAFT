#!/usr/bin/env python3
"""Does the negative sampler discriminate on TRAINING programs?

For every curated training program that has a reference answer, score a
ladder of answers with the real reward (Houdini filter + current negative
sampler) and check the ordering the reward must respect:

    gold      reference answer (Houdini-valid, contains relational clauses)
    loose     gold minus every relational clause (bounds / frame only)
    trivial   ``1 == 1``
    guard     the loop guard copied as an invariant (false at exit)
    unsound   one variable pinned to a sampled constant (false somewhere)

Expected: gold > loose by a margin, junk families ~0, loose far from 1.0.
A second check simulates a GRPO group: 8 random sub-answers of gold are
scored together and the within-group reward spread is recorded -- a group
with zero spread carries no gradient.  Everything is target-independent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import random
import resource
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts.audit_sft_invariant_quality import _clause_features  # noqa: E402
from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    _atomic_json,
    _display_path,
    _source_from_rl,
    _source_from_sft,
)
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402
from rl_pipeline.common.state import extract_invariants  # noqa: E402
from rl_pipeline.reward.filters import PreFramaFilter, auto_filter  # noqa: E402
from rl_pipeline.reward.reward_calculator import RewardCalculator  # noqa: E402
from rl_pipeline.sampler.example_sampler import NEGATIVE_SCHEMA_VERSION, ExampleSampler  # noqa: E402

_CALC: Optional[RewardCalculator] = None


def _init(filter_mode: str, memory_cap: int) -> None:
    global _CALC
    if memory_cap:
        resource.setrlimit(resource.RLIMIT_AS, (memory_cap, memory_cap))
    _CALC = RewardCalculator(
        invariant_filter=PreFramaFilter() if filter_mode == "lite" else auto_filter(), n_jobs=1,
    )


def _job(job: Tuple[str, str, List[str], int]) -> dict:
    digest, source, gold, seed = job
    try:
        assert _CALC is not None
        program = parse_program(strip_postcondition(source))
        modified = set(ExampleSampler._modified_vars(program))
        features = [_clause_features(c, program, modified) for c in gold]
        loose = [c for c, f in zip(gold, features) if not f["transition_law"]]
        guard = program.loops[0].guard.strip() if program.loops and program.loops[0].guard else ""
        pin_var = (modified or set(program.pre_vars) or {"x"})
        pin = sorted(pin_var)[0]
        families = {
            "gold": gold,
            "loose": loose or ["1 == 1"],
            "trivial": ["1 == 1"],
            "guard": [guard] if guard and guard not in ("1", "true") else ["1 == 1"],
            "unsound": [f"{pin} == 123456789"],
        }
        names = list(families)
        batch = _CALC.compute(source, [families[n] for n in names])
        rewards = {n: round(r.reward, 4) for n, r in zip(names, batch.rollouts)}
        bases = {n: round(r.base, 4) for n, r in zip(names, batch.rollouts)}
        # Simulated GRPO group: 8 random sub-answers of gold.
        rng = random.Random(f"{seed}:{digest}")
        group = []
        for _ in range(8):
            size = rng.randint(1, max(1, len(gold)))
            group.append(rng.sample(gold, size))
        gbatch = _CALC.compute(source, group)
        grewards = [r.reward for r in gbatch.rollouts]
        return {
            "digest": digest, "status": "ok", "n_negatives": batch.n_negatives,
            "scorable": batch.scorable, "n_gold": len(gold), "n_loose": len(loose),
            "rewards": rewards, "base": bases,
            "group_rewards": [round(r, 4) for r in grewards],
            "group_mean": round(statistics.fmean(grewards), 4),
            "group_std": round(statistics.pstdev(grewards), 4) if len(grewards) > 1 else 0.0,
            "group_distinct": len({round(r, 3) for r in grewards}),
        }
    except Exception as error:
        return {"digest": digest, "status": "error", "error": f"{type(error).__name__}: {str(error)[:200]}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rl", type=Path, default=ROOT / "traindata/craft_rl_train.parquet")
    parser.add_argument("--answers", type=Path, action="append", required=True,
                        help="SFT-format json(s) providing reference answers (repeatable)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--filter", choices=("auto", "lite"), default="auto")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--memory-cap", type=int, default=3_000_000_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--margin", type=float, default=0.15)
    args = parser.parse_args()

    answers: Dict[str, List[str]] = {}
    for path in args.answers:
        for record in json.loads(path.read_text(encoding="utf-8")):
            source = _source_from_sft(record)
            clauses = extract_invariants(next(t["value"] for t in record["conversations"] if t["from"] == "gpt"))
            if clauses:
                answers.setdefault(hashlib.sha256(source.encode("utf-8")).hexdigest(), clauses)
    jobs = []
    for row in pq.read_table(args.rl, columns=["prompt"]).to_pylist():
        source = _source_from_rl(row)
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if digest in answers:
            jobs.append((digest, source, answers[digest], args.seed))
    jobs.sort()
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"programs with reference answers: {len(jobs)}", flush=True)

    results = []
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx, initializer=_init,
                             initargs=(args.filter, args.memory_cap)) as pool:
        futures = [pool.submit(_job, job) for job in jobs]
        for count, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if count % 25 == 0 or count == len(futures):
                print(f"[audit] {count}/{len(futures)}", flush=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    ok = [r for r in results if r["status"] == "ok" and r["scorable"]]
    def share(pred) -> float:
        return round(sum(1 for r in ok if pred(r)) / len(ok), 4) if ok else None
    def q(values, p):
        values = sorted(values)
        return round(values[min(len(values) - 1, int(p * len(values)))], 4) if values else None
    report = {
        "schema_version": 1,
        "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
        "filter": args.filter,
        "rl_input": _display_path(args.rl),
        "programs": len(jobs),
        "scored": len(ok),
        "errors": Counter(r.get("error", "")[:40] for r in results if r["status"] != "ok"),
        "unscorable": sum(1 for r in results if r["status"] == "ok" and not r["scorable"]),
        "reward_medians": {f: q([r["rewards"][f] for r in ok], 0.5) for f in ("gold", "loose", "trivial", "guard", "unsound")},
        "reward_means": {f: round(statistics.fmean(r["rewards"][f] for r in ok), 4) for f in ("gold", "loose", "trivial", "guard", "unsound")} if ok else {},
        "gold_above_loose_by_margin": share(lambda r: r["rewards"]["gold"] >= r["rewards"]["loose"] + args.margin),
        "gold_strictly_above_loose": share(lambda r: r["rewards"]["gold"] > r["rewards"]["loose"]),
        "loose_saturated_ge_0.9": share(lambda r: r["rewards"]["loose"] >= 0.9),
        "gold_at_zero": share(lambda r: r["rewards"]["gold"] <= 0.0),
        "junk_nonzero": {f: share(lambda r, f=f: r["rewards"][f] > 0.0) for f in ("trivial", "guard", "unsound")},
        "junk_above_gold": share(lambda r: max(r["rewards"][f] for f in ("trivial", "guard", "unsound")) >= r["rewards"]["gold"]),
        "group_std_median": q([r["group_std"] for r in ok], 0.5),
        "group_std_p25": q([r["group_std"] for r in ok], 0.25),
        "group_zero_variance_share": share(lambda r: r["group_std"] < 0.01),
        "group_distinct_median": q([r["group_distinct"] for r in ok], 0.5),
        "policy": "ladder scored with the production RewardCalculator on target-hidden training programs; "
                  "gold = reference answer, loose = gold minus relational clauses; group = 8 random "
                  "sub-answers of gold scored as one GRPO group",
    }
    report["errors"] = dict(report["errors"])
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
