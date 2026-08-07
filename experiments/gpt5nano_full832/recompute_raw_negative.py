"""Recompute negative rejection from every tool's final output, without Houdini."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import time

from .common import (
    DEFAULT_RESULTS_ROOT,
    METHODS,
    Task,
    append_jsonl,
    discover_tasks,
    latest_rows,
)
from .daikon_adapter import (
    METHOD as DAIKON_METHOD,
    load_manifest_lightweight,
    raw_negative_fields,
)
from .run import event_path
from .samples import load_sample


RAW_SCORE_PROTOCOL = "raw_final_output_no_houdini_v1"
ALL_METHODS = (*METHODS, DAIKON_METHOD)


def _one(
    task: Task,
    sample: dict,
    entries: list[tuple[str, dict]],
) -> list[tuple[str, dict]]:
    load_started = time.perf_counter()
    examples = load_sample(task, sample)
    shared_load_seconds = (time.perf_counter() - load_started) / max(len(entries), 1)
    output = []
    for method, original in entries:
        row = dict(original)
        if row.get("generation_status") == "unsupported":
            row.update({
                "raw_negative_score_protocol": RAW_SCORE_PROTOCOL,
                "negative_score_status": "not_applicable",
                "reward_mode": None,
                "positive_state_count": int(sample["positive_state_count"]),
                "negative_trace_count": None,
                "rejected_negative_count": None,
                "negative_rejection_score": None,
                "binary_frama_c_validation": None,
                "score_surviving_invariants": [],
                "negative_score_error": "unsupported native input",
                "negative_score_seconds": shared_load_seconds,
                "negative_score_time_accounting": "shared_sample_load_only",
                "negative_score_shared_batch_size": len(entries),
            })
            output.append((method, row))
            continue

        score_started = time.perf_counter()
        # Preserve the old Houdini-filtered values in the new append-only row.
        for field in (
            "reward_mode",
            "rejected_negative_count",
            "negative_rejection_score",
            "binary_frama_c_validation",
            "score_surviving_invariants",
        ):
            row[f"previous_filtered_{field}"] = row.get(field)
        row.update(raw_negative_fields(
            examples,
            row.get("invariants") or [],
            verified=row.get("verified") is True,
        ))
        row.update({
            "raw_negative_score_protocol": RAW_SCORE_PROTOCOL,
            "negative_score_status": "completed",
            "positive_state_count": int(sample["positive_state_count"]),
            "negative_score_error": None,
            "negative_score_seconds": (
                time.perf_counter() - score_started + shared_load_seconds
            ),
            "negative_score_time_accounting": (
                "raw_evaluation_plus_equal_shared_sample_load"
            ),
            "negative_score_shared_batch_size": len(entries),
            "sample_artifact": sample["sample_artifact"],
            "sample_content_sha256": sample["sample_content_sha256"],
            "sample_file_sha256": sample["sample_file_sha256"],
        })
        row["reproduction_total_seconds"] = sum(
            float(row.get(field) or 0.0)
            for field in ("generation_seconds", "judge_seconds", "negative_score_seconds")
        )
        output.append((method, row))
    return output


def recompute(root: Path, workers: int) -> None:
    samples = load_manifest_lightweight(root)
    tasks = discover_tasks()
    paths = {method: event_path(root, method) for method in ALL_METHODS}
    current = {
        method: latest_rows([path]) for method, path in paths.items()
    }
    work = []
    for task in tasks:
        entries = []
        for method in ALL_METHODS:
            row = current[method].get(task.key(method))
            if not row:
                continue
            if row.get("raw_negative_score_protocol") == RAW_SCORE_PROTOCOL:
                continue
            entries.append((method, row))
        if entries:
            work.append((task, samples[(task.suite, task.case_id)], entries))

    pending_rows = sum(len(entries) for _task, _sample, entries in work)
    print(
        f"raw negative scoring tasks={len(work)} rows={pending_rows} "
        f"workers={workers}",
        flush=True,
    )
    completed_rows = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_one, task, sample, entries): task
            for task, sample, entries in work
        }
        for future in as_completed(futures):
            task = futures[future]
            rows = future.result()
            for method, row in rows:
                append_jsonl(paths[method], row)
            completed_rows += len(rows)
            print(
                f"[{completed_rows}/{pending_rows}] {task.suite}/{task.case_id}",
                flush=True,
            )

    metadata = {
        "protocol": RAW_SCORE_PROTOCOL,
        "methods": list(ALL_METHODS),
        "task_count": len(tasks),
        "description": (
            "Final emitted invariants evaluated directly on fixed negative trace "
            "groups; no PositiveFilter or HoudiniFilter. Zero-negative tasks use "
            "the existing direct common Frama-C verification result."
        ),
    }
    (root / "raw_negative_score_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    recompute(args.results_root, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
