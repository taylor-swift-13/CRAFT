"""Materialize auditable per-task and aggregate strict AutoSpec results."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from .common import append_jsonl, latest_rows, read_jsonl


FIELDS = [
    "suite", "case_id", "generation_status", "generation_error",
    "verified", "invariant_count", "generation_seconds", "judge_seconds",
    "prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens",
    "token_accounting", "artifact", "hidden_source", "invariant_recovery",
    "invariant_recovery_log", "judge_protocol",
]


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "sum": sum(ordered),
        "mean": statistics.mean(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[int((len(ordered) - 1) * 0.95)],
        "min": ordered[0],
        "max": ordered[-1],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--publish-event-path", type=Path)
    args = parser.parse_args()

    event_path = args.results_root / "events" / "autospec.jsonl"
    rows = list(latest_rows([event_path]).values())
    rows.sort(key=lambda row: (row["suite"], int(row["case_id"])))
    if len(rows) != 832:
        raise RuntimeError(f"expected 832 latest rows, found {len(rows)}")
    if any(row.get("verified") not in (True, False) for row in rows):
        raise RuntimeError("every row must have a boolean common-judge result")
    if any(row.get("token_accounting") != "exact" for row in rows):
        raise RuntimeError("every row must have exact token accounting")
    if any(
        row["total_tokens"] != row["prompt_tokens"] + row["completion_tokens"]
        for row in rows
    ):
        raise RuntimeError("prompt + completion != total token usage")

    jsonl_path = args.results_root / "autospec_strict_final.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    csv_path = args.results_root / "autospec_strict_final.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    by_suite = {}
    for suite in ("linear", "NLA_lipus", "Loopy"):
        subset = [row for row in rows if row["suite"] == suite]
        by_suite[suite] = {
            "rows": len(subset),
            "generation_completed": sum(
                row["generation_status"] == "completed" for row in subset
            ),
            "generation_failed": sum(
                row["generation_status"] != "completed" for row in subset
            ),
            "verified": sum(row["verified"] is True for row in subset),
        }

    generation = [float(row["generation_seconds"]) for row in rows]
    judge = [float(row.get("judge_seconds") or 0.0) for row in rows]
    summary = {
        "method": "autospec",
        "strict": True,
        "rows": len(rows),
        "generation_completed": sum(
            row["generation_status"] == "completed" for row in rows
        ),
        "generation_failed": sum(
            row["generation_status"] != "completed" for row in rows
        ),
        "verified": sum(row["verified"] is True for row in rows),
        "verified_rate": sum(row["verified"] is True for row in rows) / len(rows),
        "by_suite": by_suite,
        "prompt_tokens": _stats([float(row["prompt_tokens"]) for row in rows]),
        "completion_tokens": _stats([float(row["completion_tokens"]) for row in rows]),
        "reasoning_tokens": _stats([float(row["reasoning_tokens"]) for row in rows]),
        "total_tokens": _stats([float(row["total_tokens"]) for row in rows]),
        "generation_seconds": _stats(generation),
        "judge_seconds": _stats(judge),
        "end_to_end_seconds": _stats([
            generation[index] + judge[index] for index in range(len(rows))
        ]),
        "token_accounting": "exact_api_usage_all_832",
        "judge_protocol": "target_hidden_restored_original_v1",
        "frama_c": "31.0 (Gallium)",
        "z3": "4.13.3",
        "per_task_jsonl": str(jsonl_path),
        "per_task_csv": str(csv_path),
    }
    summary_path = args.results_root / "autospec_strict_statistics.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    if args.publish_event_path is not None:
        existing = read_jsonl(args.publish_event_path)
        # Remove an earlier publication of this same strict result so this
        # operation is idempotent and cannot create a second protocol cohort.
        existing = [
            row for row in existing
            if row.get("strict_source") != str(summary_path)
        ]
        canonical = {}
        for row in existing:
            if row.get("method") == "autospec":
                canonical[(str(row["suite"]), str(row["case_id"]))] = row
        if len(canonical) != 832:
            raise RuntimeError(
                f"expected 832 canonical AutoSpec rows, found {len(canonical)}"
            )
        args.publish_event_path.parent.mkdir(parents=True, exist_ok=True)
        with args.publish_event_path.open("w", encoding="utf-8") as handle:
            for row in existing:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        for row in rows:
            key = (str(row["suite"]), str(row["case_id"]))
            published = dict(row)
            old = canonical[key]
            published["strict_generation_protocol"] = published.get("protocol")
            published["strict_generation_protocol_sha256"] = published.get(
                "protocol_sha256"
            )
            published["protocol"] = old["protocol"]
            published["protocol_sha256"] = old["protocol_sha256"]
            published["strict_source"] = str(summary_path)
            append_jsonl(args.publish_event_path, published)
        summary["published_event_path"] = str(args.publish_event_path)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
