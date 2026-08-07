"""Evaluate pass@1 and combine@1 from the same saved one-rollout rows."""
from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .common import discover_tasks, judge_invariants, latest_rows


def _api_seconds(row: dict) -> float:
    path = row.get("api_calls_artifact")
    if not path or not Path(path).exists():
        return 0.0
    try:
        calls = json.loads(Path(path).read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    return sum(float(call.get("seconds") or 0.0) for call in calls)


def _judge_pass(task, row: dict) -> dict:
    result = {
        "suite": row["suite"],
        "case_id": row["case_id"],
        "model": row["model"],
        "generation_status": row.get("generation_status"),
        "prompt_tokens": row.get("prompt_tokens"),
        "completion_tokens": row.get("completion_tokens"),
        "total_tokens": row.get("total_tokens"),
        "token_accounting": row.get("token_accounting"),
        "model_seconds": _api_seconds(row),
        "combine_total_seconds": row.get("generation_seconds"),
        "combine_verified": row.get("native_verified") is True,
    }
    if row.get("generation_status") != "completed":
        result.update({
            "pass_verified": False,
            "pass_judge_seconds": 0.0,
            "pass_judge_error": row.get("generation_error") or "generation_failed",
        })
        return result

    rollouts = row.get("rollouts") or []
    raw = rollouts[0] if rollouts else []
    judged = judge_invariants(task, raw)
    result.update({
        "raw_invariants": judged["invariants"],
        "raw_invariant_count": judged["invariant_count"],
        "pass_verified": judged["verified"] is True,
        "pass_judge_seconds": judged["judge_seconds"],
        "pass_judge_error": judged["judge_error"],
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    source_path = args.results_root / "events" / "loopgym_r1_houdini.jsonl"
    source = [
        row for row in latest_rows([source_path]).values()
        if row.get("model") == args.model
    ]
    if len(source) != 832:
        raise RuntimeError(f"expected 832 source rows, found {len(source)}")

    output_path = args.results_root / "paired_pass1_combine1.jsonl"
    completed: set[tuple[str, str]] = set()
    if args.resume and output_path.exists():
        for line in output_path.read_text(errors="ignore").splitlines():
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            completed.add((str(old["suite"]), str(old["case_id"])))

    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    pending = [
        row for row in source
        if (str(row["suite"]), str(row["case_id"])) not in completed
    ]
    mode = "a" if args.resume else "w"
    with output_path.open(mode, encoding="utf-8") as handle:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    _judge_pass,
                    tasks[(str(row["suite"]), str(row["case_id"]))],
                    row,
                ): row
                for row in pending
            }
            for index, future in enumerate(as_completed(futures), 1):
                result = future.result()
                handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                print(
                    f"[{index}/{len(pending)}] {result['suite']}/{result['case_id']} "
                    f"pass={result['pass_verified']} combine={result['combine_verified']}",
                    flush=True,
                )

    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    by_key = {(str(row["suite"]), str(row["case_id"])): row for row in rows}
    rows = list(by_key.values())
    prompt_values = [row["prompt_tokens"] for row in rows if row.get("prompt_tokens") is not None]
    completion_values = [row["completion_tokens"] for row in rows if row.get("completion_tokens") is not None]
    token_values = [row["total_tokens"] for row in rows if row.get("total_tokens") is not None]
    model_times = [float(row.get("model_seconds") or 0.0) for row in rows]
    pass_judge_times = [float(row.get("pass_judge_seconds") or 0.0) for row in rows]
    pass_times = [
        float(row.get("model_seconds") or 0.0)
        + float(row.get("pass_judge_seconds") or 0.0)
        for row in rows
    ]
    combine_times = [
        float(row["combine_total_seconds"])
        for row in rows if row.get("combine_total_seconds") is not None
    ]
    summary = {
        "model": args.model,
        "rows": len(rows),
        "pass_at_1": sum(row.get("pass_verified") is True for row in rows),
        "combine_at_1": sum(row.get("combine_verified") is True for row in rows),
        "pass_at_1_rate": sum(row.get("pass_verified") is True for row in rows) / len(rows),
        "combine_at_1_rate": sum(row.get("combine_verified") is True for row in rows) / len(rows),
        "total_prompt_tokens": sum(prompt_values),
        "total_completion_tokens": sum(completion_values),
        "total_tokens": sum(token_values),
        "mean_prompt_tokens": statistics.mean(prompt_values),
        "mean_completion_tokens": statistics.mean(completion_values),
        "mean_total_tokens_both": statistics.mean(token_values),
        "mean_model_seconds": statistics.mean(model_times),
        "mean_pass_judge_seconds": statistics.mean(pass_judge_times),
        "mean_pass_seconds": statistics.mean(pass_times),
        "mean_combine_seconds": statistics.mean(combine_times),
        "paired_outcomes": {
            "both_verified": sum(
                row.get("pass_verified") is True
                and row.get("combine_verified") is True for row in rows
            ),
            "filter_gain": sum(
                row.get("pass_verified") is not True
                and row.get("combine_verified") is True for row in rows
            ),
            "filter_regression": sum(
                row.get("pass_verified") is True
                and row.get("combine_verified") is not True for row in rows
            ),
            "neither_verified": sum(
                row.get("pass_verified") is not True
                and row.get("combine_verified") is not True for row in rows
            ),
        },
        "by_suite": {
            suite: {
                "rows": sum(row["suite"] == suite for row in rows),
                "pass_at_1": sum(
                    row["suite"] == suite and row.get("pass_verified") is True
                    for row in rows
                ),
                "combine_at_1": sum(
                    row["suite"] == suite and row.get("combine_verified") is True
                    for row in rows
                ),
            }
            for suite in ("linear", "NLA_lipus", "Loopy")
        },
    }
    summary_path = args.results_root / "paired_pass1_combine1_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    print(summary_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
