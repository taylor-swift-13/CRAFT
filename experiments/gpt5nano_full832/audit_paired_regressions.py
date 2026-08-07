"""Repeat paired pass/combine checks for apparent filter regressions."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .common import discover_tasks, judge_invariants, latest_rows


def _judge(task, invariants: list[str], suite: str, case_id: str,
           mode: str, repetition: int) -> dict:
    result = judge_invariants(task, invariants)
    return {
        "suite": suite,
        "case_id": case_id,
        "mode": mode,
        "repetition": repetition,
        "verified": result["verified"] is True,
        "judge_error": result["judge_error"],
        "judge_seconds": result["judge_seconds"],
        "invariant_count": result["invariant_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()

    source_path = args.results_root / "events" / "loopgym_r1_houdini.jsonl"
    source = {
        (str(row["suite"]), str(row["case_id"])): row
        for row in latest_rows([source_path]).values()
        if row.get("model") == args.model
    }
    paired_path = args.results_root / "paired_pass1_combine1.jsonl"
    paired = [json.loads(line) for line in paired_path.read_text().splitlines()]
    regressions = [
        row for row in paired
        if row.get("pass_verified") is True
        and row.get("combine_verified") is not True
    ]
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}

    jobs = []
    for paired_row in regressions:
        key = (str(paired_row["suite"]), str(paired_row["case_id"]))
        source_row = source[key]
        raw = (source_row.get("rollouts") or [[]])[0]
        filtered = source_row.get("invariants") or []
        for repetition in range(1, args.repetitions + 1):
            jobs.append((tasks[key], raw, *key, "pass", repetition))
            jobs.append((tasks[key], filtered, *key, "combine", repetition))

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_judge, *job) for job in jobs]
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            results.append(row)
            print(
                f"[{index}/{len(jobs)}] {row['suite']}/{row['case_id']} "
                f"{row['mode']}={row['verified']}",
                flush=True,
            )

    results.sort(key=lambda row: (
        row["suite"], int(row["case_id"]), row["mode"], row["repetition"]
    ))
    output_path = args.results_root / "paired_regression_audit.jsonl"
    output_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in results
    ))
    cases = []
    for paired_row in regressions:
        key = (str(paired_row["suite"]), str(paired_row["case_id"]))
        case_rows = [
            row for row in results
            if (row["suite"], row["case_id"]) == key
        ]
        cases.append({
            "suite": key[0],
            "case_id": key[1],
            "pass_rechecks": sum(
                row["verified"] for row in case_rows if row["mode"] == "pass"
            ),
            "combine_rechecks": sum(
                row["verified"] for row in case_rows if row["mode"] == "combine"
            ),
            "repetitions": args.repetitions,
        })
    summary = {
        "model": args.model,
        "original_regression_count": len(regressions),
        "repetitions": args.repetitions,
        "cases": cases,
    }
    summary_path = args.results_root / "paired_regression_audit_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
