#!/usr/bin/env python3
"""Compare Base, Shapley, and their mixtures on archived rollout groups.

The experiment reuses real eight-response generation groups, frozen
target-independent negative traces, and archived per-rollout target verdicts.
Each group is rescored once with the production pooled reward path.  AUROC is
computed within each program and macro-averaged so that task difficulty cannot
drive the comparison.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import random
import statistics
from typing import Iterable

import numpy as np
from sklearn.metrics import roc_auc_score

from experiments.gpt5nano_full832.common import discover_tasks, ensure_frama_c_available
from experiments.gpt5nano_full832.samples import load_sample
from rl_pipeline.reward import filters
from rl_pipeline.reward.reward_calculator import RewardCalculator


SUITES = ("linear", "NLA_lipus", "Loopy")
DEFAULT_SHARES = (0.0, 0.05, 0.10, 0.15, 0.20, 3 / 13, 0.25, 0.30,
                  0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0)
_CALCULATOR: RewardCalculator | None = None


def _init_worker(wp_timeout: int, wp_par: int) -> None:
    global _CALCULATOR
    os.environ["CRAFT_WP_TIMEOUT"] = str(wp_timeout)
    os.environ["CRAFT_WP_PAR"] = str(wp_par)
    _CALCULATOR = RewardCalculator(
        invariant_filter=filters.auto_filter(),
        credit_filter_order="pooled",
        n_jobs=1,
    )


def _score_job(job: tuple) -> dict:
    task, rollouts, verdicts, sample_row = job
    try:
        assert _CALCULATOR is not None
        examples = load_sample(task, sample_row)
        batch = _CALCULATOR.compute(
            task.hidden_source,
            rollouts,
            examples=examples,
        )
        rows = []
        for index, (score, verdict) in enumerate(zip(batch.rollouts, verdicts)):
            rows.append({
                "suite": task.suite,
                "case_id": str(task.case_id),
                "rollout_index": index,
                "target_verified": bool(verdict["verified"]),
                "base": score.base,
                "shapley": score.shapley_credit,
                "overflow": score.overflow,
                "overflow_penalty": score.overflow_penalty,
                "deployed_full": score.reward,
                "survivor_count": len(score.survivors),
                "generated_count": score.generated,
            })
        return {
            "suite": task.suite,
            "case_id": str(task.case_id),
            "status": "ok",
            "negative_groups": batch.n_negatives,
            "scorable": batch.scorable,
            "rows": rows,
        }
    except Exception as error:
        return {
            "suite": task.suite,
            "case_id": str(task.case_id),
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
        }


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _load_jobs(
    results_root: Path,
    samples_root: Path,
    group_size: int,
    per_suite: int | None,
    seed: int,
) -> list[tuple]:
    generated = {}
    with (results_root / "r10_results.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            generated[(row["suite"], str(row["case_id"]))] = ast.literal_eval(
                row["rollouts"]
            )
    judged = {
        (row["suite"], str(row["case_id"])): row["per_rollout"]
        for row in _read_jsonl(results_root / "pass10_judged.jsonl")
    }
    samples = {
        (row["suite"], str(row["case_id"])): row
        for row in _read_jsonl(samples_root / "samples_manifest.jsonl")
    }

    by_suite: dict[str, list] = defaultdict(list)
    for task in discover_tasks():
        key = (task.suite, str(task.case_id))
        if key not in generated or key not in judged or key not in samples:
            continue
        if samples[key].get("sample_status") != "completed":
            continue
        rollouts = generated[key]
        verdicts = judged[key]
        if len(rollouts) < group_size or len(verdicts) < group_size:
            continue
        by_suite[task.suite].append(
            (task, rollouts[:group_size], verdicts[:group_size], samples[key])
        )

    rng = random.Random(seed)
    jobs = []
    for suite in SUITES:
        candidates = sorted(by_suite[suite], key=lambda job: int(job[0].case_id))
        if per_suite is not None:
            if len(candidates) < per_suite:
                raise ValueError(f"{suite} has only {len(candidates)} eligible tasks")
            candidates = rng.sample(candidates, per_suite)
        jobs.extend(candidates)
    return jobs


def _task_groups(rows: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["suite"], row["case_id"])].append(row)
    return groups


def _score(row: dict, share: float) -> float:
    return (1.0 - share) * row["base"] + share * row["shapley"]


def _auc_summary(groups: dict, share: float) -> dict:
    task_auc = {}
    pair_counts = {}
    for key, rows in groups.items():
        labels = np.asarray([row["target_verified"] for row in rows], dtype=int)
        if labels.min() == labels.max():
            continue
        scores = [_score(row, share) for row in rows]
        task_auc[key] = float(roc_auc_score(labels, scores))
        pair_counts[key] = int(labels.sum() * (len(labels) - labels.sum()))
    values = list(task_auc.values())
    return {
        "informative_programs": len(values),
        "macro_auroc": float(statistics.fmean(values)) if values else None,
        "pair_weighted_auroc": (
            float(np.average(values, weights=[pair_counts[key] for key in task_auc]))
            if values else None
        ),
        "task_auc": task_auc,
    }


def _deployed_auc_summary(groups: dict) -> dict:
    task_auc = {}
    for key, rows in groups.items():
        labels = np.asarray([row["target_verified"] for row in rows], dtype=int)
        if labels.min() == labels.max():
            continue
        task_auc[key] = float(roc_auc_score(
            labels, [row["deployed_full"] for row in rows]
        ))
    return {
        "informative_programs": len(task_auc),
        "macro_auroc": float(statistics.fmean(task_auc.values())) if task_auc else None,
        "task_auc": task_auc,
    }


def _bootstrap(
    summaries: list[dict],
    base_task_auc: dict,
    replicates: int,
    seed: int,
) -> None:
    keys_by_suite = {
        suite: [key for key in base_task_auc if key[0] == suite]
        for suite in SUITES
    }
    rng = np.random.default_rng(seed)
    for summary in summaries:
        task_auc = summary.pop("task_auc")
        auc_samples = []
        delta_samples = []
        for _ in range(replicates):
            selected = []
            for keys in keys_by_suite.values():
                if keys:
                    selected.extend(rng.choice(keys, size=len(keys), replace=True))
            auc_samples.append(statistics.fmean(task_auc[tuple(key)] for key in selected))
            delta_samples.append(statistics.fmean(
                task_auc[tuple(key)] - base_task_auc[tuple(key)] for key in selected
            ))
        summary["macro_auroc_ci95"] = [
            float(x) for x in np.quantile(auc_samples, [0.025, 0.975])
        ]
        summary["delta_vs_base_macro_auroc"] = (
            summary["macro_auroc"]
            - statistics.fmean(base_task_auc.values())
        )
        summary["delta_vs_base_ci95"] = [
            float(x) for x in np.quantile(delta_samples, [0.025, 0.975])
        ]


def _build_summary(
    task_results: list[dict],
    shares: tuple[float, ...],
    bootstrap: int,
    seed: int,
    configuration: dict,
) -> dict:
    good = [result for result in task_results if result["status"] == "ok"]
    rows = [row for result in good if result["scorable"] for row in result["rows"]]
    groups = _task_groups(rows)
    sweep = []
    internal = []
    for share in shares:
        metric = _auc_summary(groups, share)
        internal.append(metric)
        sweep.append({
            "shapley_share": share,
            "base_share": 1.0 - share,
            "equivalent_shapley_lambda_in_base_plus_lambda_phi": (
                share / (1.0 - share) if share < 1.0 else None
            ),
            **{key: value for key, value in metric.items() if key != "task_auc"},
        })
    base_task_auc = internal[0]["task_auc"]
    _bootstrap(internal, base_task_auc, bootstrap, seed)
    for public, computed in zip(sweep, internal):
        public.update({key: value for key, value in computed.items() if key != "task_auc"})

    deployed = _deployed_auc_summary(groups)
    _bootstrap([deployed], base_task_auc, bootstrap, seed + 1)
    by_suite = {}
    default_share = 3 / 13
    for suite in SUITES:
        suite_groups = {key: value for key, value in groups.items() if key[0] == suite}
        by_suite[suite] = {
            "base": {key: value for key, value in _auc_summary(suite_groups, 0.0).items()
                     if key != "task_auc"},
            "shapley": {key: value for key, value in _auc_summary(suite_groups, 1.0).items()
                        if key != "task_auc"},
            "default_full_no_overflow": {
                key: value for key, value in _auc_summary(suite_groups, default_share).items()
                if key != "task_auc"
            },
        }

    best = max(sweep, key=lambda row: row["macro_auroc"])
    return {
        "schema_version": 1,
        "configuration": configuration,
        "label": "archived standalone restored-target Frama-C/WP verdict",
        "primary_metric": "within-program AUROC, macro-averaged over mixed-label programs",
        "scored_programs": len(good),
        "scorable_programs": len(groups),
        "scored_rollouts": len(rows),
        "informative_programs": len(base_task_auc),
        "positive_rollouts": sum(row["target_verified"] for row in rows),
        "overflow_rollouts": sum(row["overflow"] > 0 for row in rows),
        "errors": [result for result in task_results if result["status"] != "ok"],
        "mixture_definition": (
            "score_q=(1-q)*Base+q*Shapley; current Base+0.3*Shapley has "
            "q=3/13 and the same ranking when overflow is zero"
        ),
        "sweep": sweep,
        "deployed_full_base_plus_0.3_shapley_minus_overflow": {
            key: value for key, value in deployed.items() if key != "task_auc"
        },
        "best_observed_mixture": best,
        "by_suite": by_suite,
        "interpretation_limit": (
            "This evaluates prediction of standalone target verification. It does not "
            "measure a rollout's causal marginal contribution to pooled composition."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root", type=Path,
        default=Path("results/01_rq1_main_verification/gpt56luna_full832_r8"),
    )
    parser.add_argument(
        "--samples-root", type=Path,
        default=Path("results/01_rq1_main_verification/gpt5_full832_r10_no_reasoning"),
    )
    parser.add_argument("--output-rows", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--group-size", type=int, default=8)
    parser.add_argument("--per-suite", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument("--wp-par", type=int, default=1)
    parser.add_argument("--bootstrap", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260905)
    args = parser.parse_args()

    ensure_frama_c_available()
    jobs = _load_jobs(
        args.results_root, args.samples_root, args.group_size,
        args.per_suite, args.seed,
    )
    print(f"eligible real rollout groups: {len(jobs)}", flush=True)
    results = []
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(args.wp_timeout, args.wp_par),
    ) as pool:
        futures = [pool.submit(_score_job, job) for job in jobs]
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if index % 20 == 0 or index == len(futures):
                errors = sum(row["status"] != "ok" for row in results)
                print(f"[{index}/{len(futures)}] errors={errors}", flush=True)

    results.sort(key=lambda row: (SUITES.index(row["suite"]), int(row["case_id"])))
    args.output_rows.parent.mkdir(parents=True, exist_ok=True)
    with args.output_rows.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, sort_keys=True) + "\n")

    configuration = {
        "results_root": str(args.results_root.resolve()),
        "samples_root": str(args.samples_root.resolve()),
        "group_size": args.group_size,
        "per_suite": args.per_suite,
        "workers": args.workers,
        "wp_timeout": args.wp_timeout,
        "wp_parallel": args.wp_par,
        "bootstrap_replicates": args.bootstrap,
        "seed": args.seed,
    }
    summary = _build_summary(
        results, DEFAULT_SHARES, args.bootstrap, args.seed, configuration
    )
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "scored_programs": summary["scored_programs"],
        "informative_programs": summary["informative_programs"],
        "errors": len(summary["errors"]),
        "best_observed_mixture": summary["best_observed_mixture"],
        "output_summary": str(args.output_summary),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
