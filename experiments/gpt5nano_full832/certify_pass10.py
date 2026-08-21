"""Compute pass@10 (any of 10 saved rollouts verifies standalone) for an r10 run.

combine@10 (native_verified in the r10 event stream) unions clauses across all
ten rollouts before Houdini/WP.  pass@10 instead judges each rollout's raw
response alone, exactly like certify_paired_r1.py judges the single r1
rollout, and reports whether at least one of the ten independently proves the
target.  This never requires new model calls; it only re-runs the same
Frama-C/WP judge already used for pass@1.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .common import discover_tasks, ensure_frama_c_available, judge_invariants, latest_rows
from .run import event_path

METHOD = "loopgym_r10_houdini"


def _judge_task(job: tuple) -> dict:
    task, row = job
    started = time.perf_counter()
    rollouts = row.get("rollouts") or []
    per_rollout = []
    verified_any = False
    for raw in rollouts:
        judged = judge_invariants(task, raw)
        per_rollout.append({
            "verified": judged["verified"],
            "invariant_count": judged["invariant_count"],
            "judge_error": judged["judge_error"],
        })
        if judged["verified"] is True:
            verified_any = True
    return {
        "suite": row["suite"],
        "case_id": row["case_id"],
        "rollout_count": len(rollouts),
        "pass10_verified": verified_any,
        "per_rollout": per_rollout,
        "judge_seconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    ensure_frama_c_available()

    source = event_path(args.results_root, METHOD)
    # Match by (suite, case_id) alone: archived runs may carry an older
    # protocol_sha256 than the live config, which the 5-tuple row_key would
    # otherwise treat as a non-match.
    by_suite_case = {
        (row["suite"], row["case_id"]): row for row in latest_rows([source]).values()
    }
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    jobs = []
    for key, task in tasks.items():
        row = by_suite_case.get(key)
        if not row or row.get("generation_status") != "completed":
            continue
        jobs.append((task, row))
    print(f"pass@10 judging {len(jobs)}/{len(tasks)} completed tasks, workers={args.workers}")

    output_path = args.results_root / "pass10_judged.jsonl"
    results = []
    with output_path.open("w", encoding="utf-8") as handle:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_judge_task, job): job for job in jobs}
            for completed, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                handle.write(json.dumps(result) + "\n")
                if completed % 100 == 0 or completed == len(jobs):
                    print(f"[{completed}/{len(jobs)}]", flush=True)

    verified = sum(r["pass10_verified"] for r in results)
    summary = {
        "method": METHOD,
        "task_rows": len(tasks),
        "judged_rows": len(results),
        "pass10_verified": verified,
        "pass10_accuracy": verified / 832,
    }
    (args.results_root / "pass10_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
