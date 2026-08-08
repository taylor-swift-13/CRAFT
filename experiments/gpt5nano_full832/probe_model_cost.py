"""Run a deterministic stratified cost probe for paired pass@1/combine@1."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import statistics

from .common import (
    append_jsonl,
    discover_tasks,
    ensure_frama_c_available,
    read_jsonl,
)
from .run import _run_loopgym_houdini


STRATA = {"linear": 8, "NLA_lipus": 1, "Loopy": 11}


def select_tasks(seed: int):
    rng = random.Random(seed)
    tasks = discover_tasks()
    selected = []
    for suite, count in STRATA.items():
        pool = [task for task in tasks if task.suite == suite]
        selected.extend(rng.sample(pool, count))
    return sorted(selected, key=lambda task: (task.suite, int(task.case_id)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--run-limit", type=int)
    args = parser.parse_args()

    model = os.environ.get("LOOPGYM_MODEL")
    if not model:
        raise RuntimeError("LOOPGYM_MODEL is required")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")
    ensure_frama_c_available()

    args.results_root.mkdir(parents=True, exist_ok=True)
    selected = select_tasks(args.seed)
    manifest = {
        "model": model,
        "reasoning_effort": None,
        "seed": args.seed,
        "strata": STRATA,
        "sample_size": len(selected),
        "tasks": [
            {"suite": task.suite, "case_id": task.case_id}
            for task in selected
        ],
    }
    (args.results_root / "probe_protocol.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    events_path = args.results_root / "probe_results.jsonl"
    existing = {}
    if events_path.exists():
        for row in read_jsonl(events_path):
            existing[(str(row["suite"]), str(row["case_id"]))] = row
    pending = [
        task for task in selected
        if existing.get((task.suite, task.case_id), {}).get("generation_status")
        != "completed"
    ]
    if args.run_limit is not None:
        pending = pending[: args.run_limit]

    def run(task):
        return _run_loopgym_houdini(
            task,
            args.results_root,
            method="loopgym_r1_houdini",
            n_rollouts=1,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, task): task for task in pending}
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            append_jsonl(events_path, row)
            print(
                f"[{index}/{len(pending)}] {row['suite']}/{row['case_id']} "
                f"{row['generation_status']} tokens={row.get('total_tokens')} "
                f"seconds={row.get('generation_seconds')}",
                flush=True,
            )

    rows_by_key = {}
    if events_path.exists():
        for row in read_jsonl(events_path):
            rows_by_key[(str(row["suite"]), str(row["case_id"]))] = row
    rows = [
        rows_by_key[(task.suite, task.case_id)]
        for task in selected
        if (task.suite, task.case_id) in rows_by_key
    ]
    completed = [row for row in rows if row.get("generation_status") == "completed"]
    exact = [
        row for row in completed
        if row.get("token_accounting") == "exact"
        and row.get("total_tokens") is not None
    ]
    timed = [row for row in completed if row.get("generation_seconds") is not None]
    summary = {
        **manifest,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "completed": len(completed),
        "failed": len(rows) - len(completed),
        "exact_token_rows": len(exact),
        "mean_total_tokens": (
            statistics.mean(row["total_tokens"] for row in exact) if exact else None
        ),
        "mean_combine_seconds": (
            statistics.mean(row["generation_seconds"] for row in timed)
            if timed else None
        ),
        "total_tokens": sum(row["total_tokens"] for row in exact),
        "combine_verified_in_sample": sum(
            row.get("native_verified") is True for row in completed
        ),
    }
    (args.results_root / "probe_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
