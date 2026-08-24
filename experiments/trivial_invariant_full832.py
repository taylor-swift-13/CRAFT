"""Empirically score and target-judge the tautology 1 == 1 on all 832 tasks.

The target-bearing result measures how many benchmark goals need no useful loop
invariant. On frozen zero-negative tasks, the second check exercises the exact
RewardCalculator fallback and verifies that the tautology receives zero reward.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path

from experiments.gpt5nano_full832.common import (
    DEFAULT_RESULTS_ROOT,
    Task,
    discover_tasks,
    ensure_frama_c_available,
    judge_invariants,
)
from rl_pipeline.common.program import parse_program
from rl_pipeline.reward.reward_calculator import RewardCalculator
from rl_pipeline.sampler import ExampleSet


INVARIANT = "1 == 1"
PROTOCOL = "trivial_invariant_full832_v1"


def evaluate(task: Task, zero_negative: bool) -> dict:
    judged = judge_invariants(task, [INVARIANT])
    fallback_reward = None
    fallback_survivors = None
    fallback_error = None
    if zero_negative:
        try:
            examples = ExampleSet(
                program=parse_program(task.hidden_source),
                positives={0: []},
                negatives={0: []},
                neg_groups={0: []},
            )
            rollout = RewardCalculator(n_jobs=1).compute(
                task.hidden_source,
                [[INVARIANT]],
                examples=examples,
            ).rollouts[0]
            fallback_reward = rollout.reward
            fallback_survivors = rollout.survivors
        except Exception as error:  # keep the full sweep auditable
            fallback_error = f"{type(error).__name__}: {error}"
    return {
        "protocol": PROTOCOL,
        "suite": task.suite,
        "case_id": task.case_id,
        "invariant": INVARIANT,
        "zero_negative": zero_negative,
        "target_verified": judged["verified"],
        "target_judge_error": judged["judge_error"],
        "target_judge_seconds": judged["judge_seconds"],
        "fallback_reward": fallback_reward,
        "fallback_survivors": fallback_survivors,
        "fallback_error": fallback_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root", type=Path, default=DEFAULT_RESULTS_ROOT
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    ensure_frama_c_available()
    manifest = {
        (row["suite"], str(row["case_id"])): row
        for row in (
            json.loads(line)
            for line in (
                args.results_root / "samples_manifest.jsonl"
            ).read_text().splitlines()
            if line.strip()
        )
    }
    tasks = discover_tasks()
    if len(tasks) != 832 or len(manifest) != 832:
        raise RuntimeError("expected a complete 832-task input and sample manifest")

    output = args.output or (
        args.results_root / "trivial_invariant_full832.jsonl"
    )
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                evaluate,
                task,
                int(manifest[(task.suite, task.case_id)]["negative_trace_count"])
                == 0,
            ): task
            for task in tasks
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            row = future.result()
            rows.append(row)
            if index % 25 == 0 or index == len(tasks):
                print(
                    f"[{index}/{len(tasks)}] {task.suite}/{task.case_id}",
                    flush=True,
                )
    rows.sort(key=lambda row: (row["suite"], int(row["case_id"])))
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    zero = [row for row in rows if row["zero_negative"]]
    summary = {
        "protocol": PROTOCOL,
        "tasks": len(rows),
        "target_verified": sum(row["target_verified"] is True for row in rows),
        "target_judge_errors": sum(
            row["target_verified"] is None for row in rows
        ),
        "zero_negative_tasks": len(zero),
        "zero_negative_fallback_zero_reward": sum(
            row["fallback_reward"] == 0.0 for row in zero
        ),
        "zero_negative_fallback_errors": sum(
            row["fallback_error"] is not None for row in zero
        ),
    }
    summary_path = output.with_name(output.stem + "_summary.json")
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(output)
    print(summary_path)
    print(json.dumps(summary, sort_keys=True))
    return int(
        summary["target_judge_errors"]
        or summary["zero_negative_fallback_errors"]
        or summary["zero_negative_fallback_zero_reward"] != len(zero)
    )


if __name__ == "__main__":
    raise SystemExit(main())
