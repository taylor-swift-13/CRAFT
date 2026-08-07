from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Callable, Iterable

from .common import DEFAULT_RESULTS_ROOT, METHODS


SESSION_IDLE_SECONDS = 300.0
V1_PROTOCOL = "loopgym-gpt5nano-full832-v1"
V2_PROTOCOL = "loopgym-gpt5nano-full832-v2"


@dataclass(frozen=True)
class Interval:
    started: datetime
    finished: datetime
    suite: str
    case_id: str


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _generation_intervals(
    path: Path,
    predicate: Callable[[dict], bool],
    *,
    timestamp_is_completion: bool,
    expected: int,
) -> list[Interval]:
    """Recover intervals from the first, generation-only event for each task."""
    intervals = []
    seen = set()
    for row in _read_jsonl(path):
        if not predicate(row) or "verified" in row:
            continue
        event_utc = row.get("event_utc")
        seconds = row.get("generation_seconds")
        if not event_utc or seconds is None:
            continue
        key = (
            str(row.get("suite")),
            str(row.get("case_id")),
            str(event_utc),
            float(seconds),
        )
        if key in seen:
            continue
        seen.add(key)
        event = datetime.fromisoformat(str(event_utc))
        duration = timedelta(seconds=float(seconds))
        started, finished = (
            (event - duration, event)
            if timestamp_is_completion
            else (event, event + duration)
        )
        intervals.append(Interval(
            started=started,
            finished=finished,
            suite=str(row["suite"]),
            case_id=str(row["case_id"]),
        ))
    if len(intervals) != expected:
        raise RuntimeError(
            f"{path.name}: expected {expected} generation intervals, "
            f"recovered {len(intervals)}"
        )
    return intervals


def _session_summary(
    intervals: Iterable[Interval], *, idle_seconds: float
) -> dict:
    ordered = sorted(intervals, key=lambda item: item.started)
    if not ordered:
        raise ValueError("cannot summarize an empty interval list")
    sessions: list[list[datetime]] = []
    for item in ordered:
        if (
            not sessions
            or (item.started - sessions[-1][1]).total_seconds() > idle_seconds
        ):
            sessions.append([item.started, item.finished])
        elif item.finished > sessions[-1][1]:
            sessions[-1][1] = item.finished
    active_seconds = sum(
        (finished - started).total_seconds()
        for started, finished in sessions
    )
    return {
        "started_utc": ordered[0].started.isoformat(),
        "finished_utc": max(item.finished for item in ordered).isoformat(),
        "calendar_envelope_seconds": (
            max(item.finished for item in ordered) - ordered[0].started
        ).total_seconds(),
        "active_wall_seconds": active_seconds,
        "session_count": len(sessions),
        "sessions": [
            {
                "started_utc": started.isoformat(),
                "finished_utc": finished.isoformat(),
                "seconds": (finished - started).total_seconds(),
            }
            for started, finished in sessions
        ],
    }


def _event_component(
    *,
    name: str,
    path: Path,
    predicate: Callable[[dict], bool],
    timestamp_is_completion: bool,
    expected: int,
) -> dict:
    intervals = _generation_intervals(
        path,
        predicate,
        timestamp_is_completion=timestamp_is_completion,
        expected=expected,
    )
    summary = _session_summary(
        intervals, idle_seconds=SESSION_IDLE_SECONDS
    )
    return {
        "component": name,
        "tasks": expected,
        "timing_basis": (
            "generation event timestamp plus/minus per-task perf_counter duration; "
            f"sessions split after {SESSION_IDLE_SECONDS:g}s with no active task"
        ),
        **summary,
    }


def _legacy_component(name: str, metadata: dict) -> dict:
    started = datetime.fromisoformat(metadata["started_utc"])
    finished = datetime.fromisoformat(metadata["completed_utc"])
    seconds = float(metadata["batch_seconds"])
    observed = (finished - started).total_seconds()
    if abs(observed - seconds) > 0.01:
        raise RuntimeError(f"{name}: legacy batch timestamps disagree")
    return {
        "component": name,
        "tasks": 366,
        "timing_basis": metadata["time_evidence"],
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "calendar_envelope_seconds": seconds,
        "active_wall_seconds": seconds,
        "session_count": 1,
        "sessions": [{
            "started_utc": started.isoformat(),
            "finished_utc": finished.isoformat(),
            "seconds": seconds,
        }],
    }


def _clause2inv_component(clause_root: Path) -> dict:
    runner = clause_root / "runner.log"
    results = clause_root / "results.jsonl"
    if not runner.exists() or not results.exists():
        raise FileNotFoundError(
            "Clause2Inv timing evidence is missing: "
            f"{runner} or {results}"
        )
    started = datetime.fromtimestamp(runner.stat().st_mtime, timezone.utc)
    finished = datetime.fromtimestamp(results.stat().st_mtime, timezone.utc)
    seconds = (finished - started).total_seconds()
    return {
        "component": "native Clause2Inv supported-366 batch",
        "tasks": 366,
        "timing_basis": "runner.log start mtime to completed results.jsonl mtime",
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "calendar_envelope_seconds": seconds,
        "active_wall_seconds": seconds,
        "session_count": 1,
        "sessions": [{
            "started_utc": started.isoformat(),
            "finished_utc": finished.isoformat(),
            "seconds": seconds,
        }],
    }


def _token_and_task_time(rows: list[dict]) -> dict:
    accounting_kinds = ("exact", "estimated", "unavailable", "not_called")
    result = {}
    for kind in accounting_kinds:
        selected = [row for row in rows if row.get("token_accounting") == kind]
        result[f"token_rows_{kind}"] = len(selected)
        result[f"tokens_{kind}"] = sum(
            int(row["total_tokens"])
            for row in selected
            if row.get("total_tokens") is not None
        )
    timed = [
        row for row in rows
        if not row.get("generation_batch_id")
        and row.get("token_accounting") != "not_called"
        and row.get("generation_seconds") is not None
    ]
    result["task_seconds_known"] = sum(
        float(row["generation_seconds"]) for row in timed
    )
    result["task_time_rows"] = len(timed)
    result["mean_task_seconds"] = (
        result["task_seconds_known"] / len(timed) if timed else None
    )
    result["mean_tokens_exact"] = (
        result["tokens_exact"] / result["token_rows_exact"]
        if result["token_rows_exact"] else None
    )
    result["mean_tokens_estimated"] = (
        result["tokens_estimated"] / result["token_rows_estimated"]
        if result["token_rows_estimated"] else None
    )
    return result


def _fmt_millions(value: int) -> str:
    return "--" if not value else f"{value / 1_000_000:.2f}M"


def _fmt_hours(value: float) -> str:
    return f"{value / 3600:.2f} h"


def _fmt_mean(value: float | None, rows: int, *, unit: str) -> str:
    if value is None:
        return "--"
    return f"{value:.4f} {unit} / {rows} rows"


def recompute(
    results_root: Path,
    *,
    clause_root: Path,
    legacy_audit_path: Path,
) -> list[dict]:
    latest = _read_jsonl(results_root / "latest.jsonl")
    latest_by_method = {
        method: [row for row in latest if row.get("method") == method]
        for method in METHODS
    }
    if any(len(rows) != 832 for rows in latest_by_method.values()):
        raise RuntimeError("latest.jsonl must contain 832 rows for every method")

    events = results_root / "events"
    legacy = json.loads(legacy_audit_path.read_text())
    components = {
        "autospec": [_event_component(
            name="fresh full-832 AutoSpec generation",
            path=events / "autospec.jsonl",
            predicate=lambda row: (
                row.get("method") == "autospec"
                and row.get("protocol") == V1_PROTOCOL
                and row.get("generation_eligible") is True
                and "/artifacts/autospec/" in str(row.get("hidden_source"))
            ),
            timestamp_is_completion=True,
            expected=832,
        )],
        "clause2inv": [_clause2inv_component(clause_root)],
        "sespec": [_event_component(
            name="fresh full-832 SESpec generation",
            path=events / "sespec.jsonl",
            predicate=lambda row: (
                row.get("method") == "sespec"
                and row.get("protocol") == V1_PROTOCOL
                and row.get("generation_eligible") is True
                and "/artifacts/sespec/" in str(row.get("hidden_source"))
            ),
            timestamp_is_completion=True,
            expected=832,
        )],
        "naive": [_event_component(
            name="fresh full-832 true-Naive generation",
            path=events / "naive.jsonl",
            predicate=lambda row: (
                row.get("method") == "naive"
                and row.get("protocol") == V2_PROTOCOL
                and bool(row.get("api_calls_artifact"))
            ),
            timestamp_is_completion=False,
            expected=832,
        )],
        "loopgym_r1_no_houdini": [
            _legacy_component("reused Core-366 R1-NoH batch", legacy["naive"]),
            _event_component(
                name="fresh Loopy-466 R1-NoH generation",
                path=events / "naive.jsonl",
                predicate=lambda row: (
                    row.get("method") == "naive"
                    and row.get("protocol") == V1_PROTOCOL
                    and row.get("suite") == "Loopy"
                    and bool(row.get("api_calls_artifact"))
                ),
                timestamp_is_completion=False,
                expected=466,
            ),
        ],
        "loopgym_r1_houdini": [_event_component(
            name="fresh full-832 R1-H generation",
            path=events / "loopgym_r1_houdini.jsonl",
            predicate=lambda row: (
                row.get("method") == "loopgym_r1_houdini"
                and row.get("protocol") == V2_PROTOCOL
                and bool(row.get("api_calls_artifact"))
            ),
            timestamp_is_completion=False,
            expected=832,
        )],
        "loopgym_r4_houdini": [
            _legacy_component("reused Core-366 R4-H batch", legacy["loopgym"]),
            _event_component(
                name="fresh Loopy-466 R4-H generation",
                path=events / "loopgym.jsonl",
                predicate=lambda row: (
                    row.get("method") == "loopgym"
                    and row.get("protocol") == V1_PROTOCOL
                    and row.get("suite") == "Loopy"
                    and bool(row.get("api_calls_artifact"))
                ),
                timestamp_is_completion=False,
                expected=466,
            ),
        ],
    }

    detail = []
    output = []
    for method in METHODS:
        method_components = components[method]
        for component in method_components:
            detail.append({"method": method, **component})
        token_time = _token_and_task_time(latest_by_method[method])
        output.append({
            "method": method,
            **token_time,
            "active_wall_seconds": sum(
                float(item["active_wall_seconds"])
                for item in method_components
            ),
            "batch_component_count": len(method_components),
            "batch_tasks_timed": sum(int(item["tasks"]) for item in method_components),
        })

    fields = list(output[0])
    with (results_root / "efficiency_reaudit.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)

    detail_fields = [
        "method", "component", "tasks", "timing_basis", "started_utc",
        "finished_utc", "calendar_envelope_seconds", "active_wall_seconds",
        "session_count", "sessions",
    ]
    with (results_root / "efficiency_batches.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=detail_fields)
        writer.writeheader()
        writer.writerows(detail)

    labels = {
        "autospec": "AutoSpec",
        "clause2inv": "Clause2Inv",
        "sespec": "SESpec",
        "naive": "Naive",
        "loopgym_r1_no_houdini": "R1-NoH",
        "loopgym_r1_houdini": "R1-H",
        "loopgym_r4_houdini": "R4-H",
    }
    table = [
        "| Method | Mean generation time | Mean API-reported tokens | Mean estimated tokens | Rows T/X/E/U/NC |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in output:
        row_counts = (
            f"{row['task_time_rows']}/{row['token_rows_exact']}/"
            f"{row['token_rows_estimated']}/"
            f"{row['token_rows_unavailable']}/{row['token_rows_not_called']}"
        )
        table.append(
            f"| {labels[row['method']]} | "
            f"{_fmt_mean(row['mean_task_seconds'], row['task_time_rows'], unit='s')} | "
            f"{_fmt_mean(row['mean_tokens_exact'], row['token_rows_exact'], unit='tokens')} | "
            f"{_fmt_mean(row['mean_tokens_estimated'], row['token_rows_estimated'], unit='tokens')} | "
            f"{row_counts} |"
        )
    report = "\n".join([
        "# Full-832 efficiency re-audit",
        "",
        *table,
        "",
        "T/X/E/U/NC means rows with per-task time, exact tokens, estimated "
        "tokens, unavailable tokens, and no model call. Averages use the row "
        "count printed with the value; API-reported and estimated tokens are "
        "never combined.",
        "",
        "Active wall time is the sum of reconstructed generation sessions, "
        "not the sum of parallel task durations. A new session is inferred "
        f"after {SESSION_IDLE_SECONDS:g} seconds with no active task. Reused "
        "Core-366 batches use their saved launch/completion timestamps. "
        "Clause2Inv uses its runner/results file timestamps.",
        "",
        "Mean generation time is the arithmetic mean of per-task "
        "`generation_seconds`. It is unavailable for the reused Core-366 "
        "R1-NoH and R4-H rows, and excludes Clause2Inv inputs on which the tool "
        "was not called. Full-precision values and active batch wall time remain "
        "available in the CSV evidence files.",
        "",
    ])
    (results_root / "efficiency_reaudit.md").write_text(report)
    (results_root / "efficiency_timing_evidence.json").write_text(
        json.dumps(detail, indent=2, sort_keys=True)
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-root", type=Path, default=DEFAULT_RESULTS_ROOT
    )
    parser.add_argument(
        "--clause-root",
        type=Path,
        default=Path(
            "/home/yangfp/Clause2Inv/combinator/runs/"
            "clause2inv_no_q_gpt5nano_yunwu"
        ),
    )
    parser.add_argument(
        "--legacy-audit",
        type=Path,
        default=Path(__file__).with_name("legacy_batches.json"),
    )
    args = parser.parse_args()
    rows = recompute(
        args.results_root,
        clause_root=args.clause_root,
        legacy_audit_path=args.legacy_audit,
    )
    print(args.results_root / "efficiency_reaudit.md")
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
