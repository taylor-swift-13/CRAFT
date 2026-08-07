from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .common import DEFAULT_RESULTS_ROOT, METHODS


EXPECTED_TASKS = 832
RESULT_FIELDS = [
    "method",
    "suite",
    "case_id",
    "generation_status",
    "generation_error",
    "invariant_count",
    "verified",
    "judge_error",
    "negative_trace_count",
    "rejected_negative_count",
    "negative_rejection_score",
    "binary_frama_c_validation",
    "generation_seconds",
    "judge_seconds",
    "negative_score_seconds",
    "reproduction_total_seconds",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "api_call_count",
    "token_accounting",
    "source",
    "hidden_source",
    "artifact",
    "sample_artifact",
    "protocol_sha256",
]
FAILURE_FIELDS = [
    "failure_kind",
    *RESULT_FIELDS,
    "negative_score_status",
    "negative_score_error",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _write_csv(
    path: Path, fields: list[str], rows: list[dict[str, Any]]
) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = {field: row.get(field) for field in RESULT_FIELDS}
    compact["invariant_count"] = len(row.get("invariants") or [])
    return compact


def _failure_kind(row: dict[str, Any]) -> str | None:
    kinds = []
    status = row.get("generation_status")
    if status == "unsupported":
        kinds.append("unsupported")
    elif status != "completed":
        kinds.append("generation")
    if "verified" not in row:
        kinds.append("unscored")
    elif row.get("verified") is not True:
        kinds.append("verification")
    if row.get("negative_score_status") not in {
        "completed",
        "not_applicable",
    }:
        kinds.append("negative_score")
    return ";".join(kinds) if kinds else None


def _percent(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return f"{100 * float(value):.2f}%"


def _hours(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return f"{float(value) / 3600:.2f} h"


def _tokens(value: Any) -> str:
    count = _int(value)
    return f"{count:,}" if count else "—"


def _mean(value: Any, rows: Any, suffix: str) -> str:
    if value in (None, ""):
        return "—"
    return f"{float(value):.2f} {suffix} ({_int(rows)} rows)"


def _int(value: Any) -> int:
    return int(float(value or 0))


def build_report(root: Path, *, allow_incomplete: bool = False) -> dict:
    latest_path = root / "latest.jsonl"
    summary_path = root / "summary.csv"
    latest = _read_jsonl(latest_path)
    with summary_path.open(newline="") as handle:
        summary = list(csv.DictReader(handle))

    all_rows = sorted(
        latest,
        key=lambda row: (
            METHODS.index(row["method"]),
            row["suite"],
            int(row["case_id"]),
        ),
    )
    compact = [_compact_row(row) for row in all_rows]
    failures = []
    for row in all_rows:
        kind = _failure_kind(row)
        if kind:
            failure = _compact_row(row)
            failure.update({
                "failure_kind": kind,
                "negative_score_status": row.get("negative_score_status"),
                "negative_score_error": row.get("negative_score_error"),
            })
            failures.append(failure)

    _write_csv(root / "final_results.csv", RESULT_FIELDS, compact)
    _write_csv(root / "failures.csv", FAILURE_FIELDS, failures)

    counts = {
        method: sum(row["method"] == method for row in all_rows)
        for method in METHODS
    }
    protocol_hashes = sorted({
        row.get("protocol_sha256") for row in all_rows
        if row.get("protocol_sha256")
    })
    models = sorted({
        row.get("model") for row in all_rows if row.get("model")
    })
    unscored = [
        f"{row['method']}:{row['suite']}/{row['case_id']}"
        for row in all_rows if "verified" not in row
    ]
    missing_sample_binding = [
        f"{row['method']}:{row['suite']}/{row['case_id']}"
        for row in all_rows if not row.get("sample_artifact")
    ]
    target_visible = [
        f"{row['method']}:{row['suite']}/{row['case_id']}"
        for row in all_rows if row.get("target_hidden") is not True
    ]
    complete = (
        counts == {method: EXPECTED_TASKS for method in METHODS}
        and models == ["gpt-5-nano"]
        and len(protocol_hashes) == 1
        and not unscored
        and not missing_sample_binding
        and not target_visible
    )
    audit = {
        "complete": complete,
        "expected_per_method": EXPECTED_TASKS,
        "rows_per_method": counts,
        "total_rows": len(all_rows),
        "models": models,
        "protocol_sha256": protocol_hashes,
        "unscored_count": len(unscored),
        "unscored": unscored,
        "missing_sample_binding_count": len(missing_sample_binding),
        "missing_sample_binding": missing_sample_binding,
        "target_visible_count": len(target_visible),
        "target_visible": target_visible,
        "failure_detail_rows": len(failures),
    }
    (root / "completion_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True)
    )

    all_summary = {
        row["method"]: row for row in summary if row["suite"] == "all"
    }
    efficiency_path = root / "efficiency_reaudit.csv"
    if not efficiency_path.exists():
        raise RuntimeError(
            "efficiency re-audit is missing; run "
            "`python3 -m experiments.gpt5nano_full832.recompute_efficiency`"
        )
    with efficiency_path.open(newline="") as handle:
        efficiency = {
            row["method"]: row for row in csv.DictReader(handle)
        }
    table = [
        "| Method | Generated | Gen OK | Failed | Timeout | Unsupported | "
        "Verified / 832 | Verified / supported | Negative micro | "
        "Negative macro | Mean gen. time | Mean API tokens | Mean est. tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        row = all_summary[method]
        cost = efficiency[method]
        supported = EXPECTED_TASKS - _int(row["unsupported"])
        verified = _int(row["verified"])
        supported_rate = verified / supported if supported else None
        table.append(
            f"| {method} | {_int(row['generated'])} | "
            f"{_int(row['completed'])} | {_int(row['failed'])} | "
            f"{_int(row['timeout'])} | {_int(row['unsupported'])} | "
            f"{verified} ({_percent(row['verified_rate'])}) | "
            f"{verified}/{supported} ({_percent(supported_rate)}) | "
            f"{_percent(row['negative_micro_rejection'])} | "
            f"{_percent(row['negative_macro_rejection'])} | "
            f"{_mean(cost['mean_task_seconds'], cost['task_time_rows'], 's')} | "
            f"{_mean(cost['mean_tokens_exact'], cost['token_rows_exact'], 'tok')} | "
            f"{_mean(cost['mean_tokens_estimated'], cost['token_rows_estimated'], 'tok')} |"
        )

    report = "\n".join([
        "# GPT-5-nano full-832 evaluation",
        "",
        f"Completion audit: **{'PASS' if complete else 'INCOMPLETE'}**.",
        "",
        *table,
        "",
        "Timing note: mean generation time is the arithmetic mean of recorded "
        "per-task durations over the displayed row count. Reused rows without "
        "per-task timing are not imputed. Reconstructed batch wall time remains "
        "in `efficiency_batches.csv`.",
        "",
        "Token note: averages use the displayed exact or estimated row count. "
        "API-reported and estimated usage are never combined; unknown usage is "
        "not imputed.",
        "",
        "Artifacts:",
        "",
        "- `final_results.csv`: one compact row per method/task.",
        "- `failures.csv`: generation, scoring, verification, and unsupported details.",
        "- `completion_audit.json`: machine-readable completeness checks.",
        "- `latest.jsonl`: full latest result objects, including raw artifact paths.",
        "- `summary.csv`: suite-level aggregate metrics and accounting metadata.",
        "- `efficiency_reaudit.csv`: corrected token and wall-time accounting.",
        "- `efficiency_batches.csv`: reconstructed batch/session timing evidence.",
        "",
    ])
    (root / "final_report.md").write_text(report)

    if not complete and not allow_incomplete:
        raise RuntimeError(
            "final report is incomplete; inspect completion_audit.json"
        )
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-root", type=Path, default=DEFAULT_RESULTS_ROOT
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    audit = build_report(
        args.results_root, allow_incomplete=args.allow_incomplete
    )
    print(args.results_root / "final_report.md")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
