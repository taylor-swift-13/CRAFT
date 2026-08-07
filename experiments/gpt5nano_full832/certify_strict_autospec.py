"""Run the common target-restored Frama-C judge on strict AutoSpec rows."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .common import append_jsonl, discover_tasks, judge_invariants, latest_rows


JUDGE_PROTOCOL = "target_hidden_restored_original_v1"


def _certify(task, row: dict) -> dict:
    result = dict(row)
    artifact = result.get("artifact")
    recovered = result.get("invariant_recovery") is not None
    if (
        result.get("generation_status") == "completed"
        and not recovered
        and (not artifact or not Path(str(artifact)).exists())
    ):
        result.update({
            "generation_status": "failed",
            "generation_error": "missing_merged_artifact",
        })

    if result.get("generation_status") != "completed":
        result.update({
            "verified": False,
            "judge_error": result.get("generation_error") or "generation_failed",
            "judge_seconds": 0.0,
        })
    else:
        result.update(judge_invariants(task, result.get("invariants") or []))
    result["judge_protocol"] = JUDGE_PROTOCOL
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    event_path = args.results_root / "events" / "autospec.jsonl"
    rows = latest_rows([event_path])
    tasks = {
        (task.suite, task.case_id): task for task in discover_tasks()
    }
    pending = []
    for row in rows.values():
        if (
            row.get("judge_protocol") == JUDGE_PROTOCOL
            and row.get("verified") in (True, False)
        ):
            continue
        task = tasks[(str(row["suite"]), str(row["case_id"]))]
        pending.append((task, row))
    if len(rows) != 832:
        raise RuntimeError(f"expected 832 latest rows, found {len(rows)}")

    print(f"strict AutoSpec judge: pending={len(pending)}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_certify, task, row): (task.suite, task.case_id)
            for task, row in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            append_jsonl(event_path, result)
            print(
                f"[{index}/{len(pending)}] {result['suite']}/{result['case_id']} "
                f"verified={result['verified']}",
                flush=True,
            )

    final_rows = latest_rows([event_path])
    values = list(final_rows.values())
    summary = {
        "rows": len(values),
        "generation_completed": sum(
            row.get("generation_status") == "completed" for row in values
        ),
        "generation_failed": sum(
            row.get("generation_status") != "completed" for row in values
        ),
        "verified": sum(row.get("verified") is True for row in values),
        "judge_protocol": JUDGE_PROTOCOL,
    }
    summary_path = args.results_root / "autospec_strict_judge_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    print(summary_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
