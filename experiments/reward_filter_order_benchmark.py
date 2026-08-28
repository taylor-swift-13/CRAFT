"""Compare pooled-first and historical independent reward filtering.

The benchmark reuses archived target-hidden rollouts and frozen synthetic
traces.  It reports wall time and discrimination of per-rollout final target
verdicts.  On a smaller subset it also compares each reward against the exact
leave-one-rollout-out (LOO) change in pooled negative coverage.

Example:

    CRAFT_WP_TIMEOUT=5 CRAFT_WP_PAR=2 python3 -m \
      experiments.reward_filter_order_benchmark \
      --per-suite 10 --loo-per-suite 2
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
from typing import Iterable

from experiments.gpt5nano_full832.common import (
    discover_tasks,
    ensure_frama_c_available,
)
from experiments.gpt5nano_full832.samples import (
    load_sample,
)
from rl_pipeline.reward import filters
from rl_pipeline.reward.reward_calculator import RewardCalculator


SUITES = ("linear", "NLA_lipus", "Loopy")


class CountingFilter:
    def __init__(self, delegate):
        self.delegate = delegate
        self.name = getattr(delegate, "name", "unknown")
        self.calls = 0

    def filter(self, *args, **kwargs):
        self.calls += 1
        return self.delegate.filter(*args, **kwargs)


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for index in order[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _roc_auc(labels: list[int], scores: list[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    ranks = _average_ranks(scores)
    rank_sum = sum(rank for rank, label in zip(ranks, labels) if label)
    return (
        rank_sum - positives * (positives + 1) / 2
    ) / (positives * negatives)


def _average_precision(labels: list[int], scores: list[float]) -> float | None:
    positives = sum(labels)
    if not positives:
        return None
    pairs = sorted(zip(scores, labels), reverse=True)
    true_positives = 0
    seen = 0
    previous_recall = 0.0
    area = 0.0
    start = 0
    while start < len(pairs):
        score = pairs[start][0]
        end = start
        while end < len(pairs) and pairs[end][0] == score:
            true_positives += pairs[end][1]
            seen += 1
            end += 1
        recall = true_positives / positives
        area += (recall - previous_recall) * (true_positives / seen)
        previous_recall = recall
        start = end
    return area


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left, right)
    )
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    if left_ss == 0 or right_ss == 0:
        return None
    return numerator / math.sqrt(left_ss * right_ss)


def _spearman(left: list[float], right: list[float]) -> float | None:
    return _pearson(_average_ranks(left), _average_ranks(right))


def _metrics(records: Iterable[dict]) -> dict:
    rows = list(records)
    labels = [int(row["target_verified"]) for row in rows]
    scores = [float(row["reward"]) for row in rows]
    return {
        "n": len(rows),
        "positives": sum(labels),
        "positive_rate": (sum(labels) / len(labels)) if labels else None,
        "roc_auc": _roc_auc(labels, scores),
        "average_precision": _average_precision(labels, scores),
    }


def _load_archives(results_root: Path):
    result_rows = {}
    with (results_root / "r10_results.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            result_rows[(row["suite"], row["case_id"])] = row
    judged_rows = {
        (row["suite"], str(row["case_id"])): row
        for line in (results_root / "pass10_judged.jsonl").read_text().splitlines()
        if line.strip()
        for row in [json.loads(line)]
    }
    return result_rows, judged_rows


def _select_tasks(
    results_root: Path,
    per_suite: int,
    seed: int,
):
    # The canonical loader validates and decompresses all 832 artifacts.  This
    # benchmark only reads a stratified subset, so validate the signed manifest
    # globally and let ``load_sample`` validate each selected payload deeply.
    manifest_path = results_root / "samples_manifest.jsonl"
    metadata = json.loads(
        (results_root / "samples_metadata.json").read_text()
    )
    manifest_text = manifest_path.read_text()
    manifest_hash = hashlib.sha256(manifest_text.encode()).hexdigest()
    if manifest_hash != metadata.get("manifest_sha256"):
        raise RuntimeError("fixed sample manifest hash mismatch")
    manifest = {
        (row["suite"], str(row["case_id"])): row
        for line in manifest_text.splitlines()
        if line.strip()
        for row in [json.loads(line)]
    }
    result_rows, judged_rows = _load_archives(results_root)
    eligible = {suite: [] for suite in SUITES}
    for task in discover_tasks():
        key = (task.suite, task.case_id)
        if (
            manifest[key].get("sample_status") == "completed"
            and key in result_rows
            and key in judged_rows
        ):
            eligible[task.suite].append(task)
    rng = random.Random(seed)
    selected = []
    for suite in SUITES:
        candidates = eligible[suite]
        if per_suite > len(candidates):
            raise ValueError(
                f"{suite} has only {len(candidates)} eligible tasks"
            )
        selected.extend(rng.sample(candidates, per_suite))
    return selected, manifest, result_rows, judged_rows


def _score(
    task,
    rollouts: list,
    examples,
    order: str,
    n_jobs: int,
):
    counting = CountingFilter(filters.auto_filter())
    calculator = RewardCalculator(
        invariant_filter=counting,
        credit_filter_order=order,
        n_jobs=n_jobs,
    )
    started = time.perf_counter()
    result = calculator.compute(
        task.hidden_source,
        rollouts,
        examples=examples,
    )
    elapsed = time.perf_counter() - started
    return result, elapsed, counting.calls


def _loo_values(task, rollouts, examples, n_jobs: int) -> list[float]:
    full, _, _ = _score(task, rollouts, examples, "pooled", n_jobs)
    values = []
    for index in range(len(rollouts)):
        reduced = rollouts[:index] + rollouts[index + 1:]
        without, _, _ = _score(task, reduced, examples, "pooled", n_jobs)
        values.append(full.batch_score - without.batch_score)
    return values


def run(args) -> dict:
    frama_c = ensure_frama_c_available()
    # Make the intended evaluation budget explicit even if the caller has a
    # different ambient configuration.
    os.environ["CRAFT_WP_TIMEOUT"] = str(args.wp_timeout)
    os.environ["CRAFT_WP_PAR"] = str(args.wp_par)
    tasks, manifest, result_rows, judged_rows = _select_tasks(
        args.results_root, args.per_suite, args.seed
    )
    loo_keys = {
        (task.suite, task.case_id)
        for suite in SUITES
        for task in [
            candidate
            for candidate in tasks
            if candidate.suite == suite
        ][:args.loo_per_suite]
    }
    records = {"pooled": [], "independent": []}
    timings = {"pooled": [], "independent": []}
    calls = {"pooled": [], "independent": []}
    loo_records = []
    task_rows = []

    for task_index, task in enumerate(tasks):
        key = (task.suite, task.case_id)
        archived = result_rows[key]
        rollouts = ast.literal_eval(archived["rollouts"])[:args.rollouts]
        verdicts = judged_rows[key]["per_rollout"][:len(rollouts)]
        examples = load_sample(task, manifest[key])
        # Alternate execution order to reduce systematic warm-up bias.
        orders = (
            ("pooled", "independent")
            if task_index % 2 == 0
            else ("independent", "pooled")
        )
        results = {}
        for order in orders:
            scored, seconds, call_count = _score(
                task, rollouts, examples, order, args.n_jobs
            )
            results[order] = scored
            timings[order].append(seconds)
            calls[order].append(call_count)
            for rollout_index, (score, verdict) in enumerate(
                zip(scored.rollouts, verdicts)
            ):
                records[order].append({
                    "suite": task.suite,
                    "case_id": task.case_id,
                    "rollout_index": rollout_index,
                    "target_verified": bool(verdict["verified"]),
                    "reward": score.reward,
                    "base": score.base,
                    "shapley_credit": score.shapley_credit,
                })

        if key in loo_keys:
            loo = _loo_values(task, rollouts, examples, args.n_jobs)
            for rollout_index, value in enumerate(loo):
                loo_records.append({
                    "suite": task.suite,
                    "case_id": task.case_id,
                    "rollout_index": rollout_index,
                    "loo_utility": value,
                    "pooled_reward": results["pooled"].rollouts[
                        rollout_index
                    ].reward,
                    "independent_reward": results["independent"].rollouts[
                        rollout_index
                    ].reward,
                })

        task_rows.append({
            "suite": task.suite,
            "case_id": task.case_id,
            "pooled_seconds": timings["pooled"][-1],
            "independent_seconds": timings["independent"][-1],
            "pooled_filter_calls": calls["pooled"][-1],
            "independent_filter_calls": calls["independent"][-1],
        })
        print(
            f"[{task_index + 1}/{len(tasks)}] {task.suite}/{task.case_id} "
            f"pooled={timings['pooled'][-1]:.2f}s "
            f"independent={timings['independent'][-1]:.2f}s",
            flush=True,
        )

    summary = {
        "configuration": {
            "results_root": str(args.results_root.resolve()),
            "seed": args.seed,
            "per_suite": args.per_suite,
            "rollouts_per_task": args.rollouts,
            "loo_per_suite": args.loo_per_suite,
            "n_jobs": args.n_jobs,
            "wp_timeout_seconds_per_obligation": args.wp_timeout,
            "wp_parallel": args.wp_par,
            "frama_c": str(frama_c),
        },
        "speed": {
            order: {
                "total_seconds": sum(timings[order]),
                "mean_seconds_per_task": statistics.fmean(timings[order]),
                "median_seconds_per_task": statistics.median(timings[order]),
                "mean_filter_calls_per_task": statistics.fmean(calls[order]),
            }
            for order in ("pooled", "independent")
        },
        "target_verdict": {
            order: {
                "overall": _metrics(records[order]),
                "by_suite": {
                    suite: _metrics(
                        row for row in records[order]
                        if row["suite"] == suite
                    )
                    for suite in SUITES
                },
            }
            for order in ("pooled", "independent")
        },
        "loo_pooled_coverage": {
            "n": len(loo_records),
            "positive_marginal_count": sum(
                row["loo_utility"] > 0 for row in loo_records
            ),
            "pooled_spearman": _spearman(
                [row["pooled_reward"] for row in loo_records],
                [row["loo_utility"] for row in loo_records],
            ),
            "independent_spearman": _spearman(
                [row["independent_reward"] for row in loo_records],
                [row["loo_utility"] for row in loo_records],
            ),
        },
        "task_rows": task_rows,
        "loo_rows": loo_records,
    }
    independent_seconds = summary["speed"]["independent"]["total_seconds"]
    pooled_seconds = summary["speed"]["pooled"]["total_seconds"]
    summary["speed"]["pooled_speedup"] = (
        independent_seconds / pooled_seconds if pooled_seconds else None
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-root",
        type=Path,
        default=Path("results/gpt5_full832_r10_no_reasoning"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--per-suite", type=int, default=10)
    parser.add_argument("--rollouts", type=int, default=8)
    parser.add_argument("--loo-per-suite", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--n-jobs", type=int, default=8)
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument("--wp-par", type=int, default=2)
    args = parser.parse_args()
    if min(
        args.per_suite,
        args.rollouts,
        args.n_jobs,
        args.wp_timeout,
        args.wp_par,
    ) < 1:
        parser.error("counts and timeouts must be positive")
    if not 0 <= args.loo_per_suite <= args.per_suite:
        parser.error("--loo-per-suite must be between 0 and --per-suite")
    summary = run(args)
    output = args.output or (
        args.results_root / "reward_filter_order_benchmark.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "speed": summary["speed"],
        "target_verdict": summary["target_verdict"],
        "loo_pooled_coverage": summary["loo_pooled_coverage"],
        "output": str(output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
