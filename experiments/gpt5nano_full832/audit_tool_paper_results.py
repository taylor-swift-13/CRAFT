"""Audit and materialize the final non-reasoning tool-comparison numbers.

The main-paper rows must come exclusively from explicit ``reasoning_effort=none``
events.  The medium-reasoning Loopy run is kept in a separate appendix record.
This script fails closed until every required 832-task run is complete.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean


SUITES = ("linear", "NLA_lipus", "Loopy")
EXPECTED = {"linear": 316, "NLA_lipus": 50, "Loopy": 466}
LOOPY_NONE_PROTOCOL = (
    "loopy_n15_as_3x5_k8_shuffle10_repair7_target_hidden_"
    "none_cap8192_top_p1_v5"
)
LOOPY_MEDIUM_PROTOCOL = (
    "loopy_n15_as_3x5_k8_shuffle10_repair7_target_hidden_"
    "medium_cap8192_top_p1_v5"
)
R10_PROTOCOL = "loopgym_r10_houdini_two_requests_n5x2_v4"


def _latest(path: Path) -> list[dict]:
    rows: dict[tuple[str, str], dict] = {}
    for line in path.read_text(errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows[(str(row.get("suite")), str(row.get("case_id")))] = row
    return list(rows.values())


def _assert_coverage(rows: list[dict], label: str) -> None:
    counts = {
        suite: sum(row.get("suite") == suite for row in rows)
        for suite in SUITES
    }
    if len(rows) != 832 or counts != EXPECTED:
        raise RuntimeError(f"{label}: incomplete coverage: {len(rows)} {counts}")


def _assert_reasoning(rows: list[dict], expected: str, label: str) -> None:
    wrong = [
        (row.get("suite"), row.get("case_id"), row.get("reasoning_effort"))
        for row in rows
        if str(row.get("reasoning_effort")).lower() != expected
    ]
    if wrong:
        raise RuntimeError(f"{label}: reasoning mismatch, examples={wrong[:5]}")


def _summarize(rows: list[dict], *, verified_field: str = "verified") -> dict:
    by_suite = {
        suite: sum(
            row.get("suite") == suite and row.get(verified_field) is True
            for row in rows
        )
        for suite in SUITES
    }
    token_values = [
        int(row["total_tokens"])
        for row in rows if row.get("total_tokens") is not None
    ]
    time_values = [
        float(row["generation_seconds"])
        for row in rows if row.get("generation_seconds") is not None
    ]
    return {
        "rows": len(rows),
        "completed": sum(row.get("generation_status") == "completed" for row in rows),
        "failed": sum(row.get("generation_status") == "failed" for row in rows),
        "by_suite": by_suite,
        "verified": sum(by_suite.values()),
        "mean_total_tokens": mean(token_values) if token_values else None,
        "token_rows": len(token_values),
        "mean_generation_seconds": mean(time_values) if time_values else None,
        "time_rows": len(time_values),
    }


def _paired(path: Path) -> dict[str, dict]:
    rows = _latest(path)
    _assert_coverage(rows, "paired pass@1/combine@1")
    result = {}
    for mode, field, time_field in (
        ("pass1", "pass_verified", "pass_judge_seconds"),
        ("combine1", "combine_verified", "combine_total_seconds"),
    ):
        by_suite = {
            suite: sum(
                row.get("suite") == suite and row.get(field) is True
                for row in rows
            )
            for suite in SUITES
        }
        tokens = [int(row["total_tokens"]) for row in rows]
        if mode == "pass1":
            times = [
                float(row.get("model_seconds") or 0)
                + float(row.get(time_field) or 0)
                for row in rows
            ]
        else:
            times = [float(row[time_field]) for row in rows]
        result[mode] = {
            "rows": 832,
            "by_suite": by_suite,
            "verified": sum(by_suite.values()),
            "mean_total_tokens": mean(tokens),
            "token_rows": len(tokens),
            "mean_generation_seconds": mean(times),
            "time_rows": len(times),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--non-reasoning-root", type=Path,
        default=Path("results/gpt5nano_tools_no_reasoning_cap8192"),
    )
    parser.add_argument(
        "--paired-root", type=Path,
        default=Path("results/gpt5nano_full832_no_reasoning_cap8192"),
    )
    parser.add_argument(
        "--medium-root", type=Path,
        default=Path("results/gpt5nano_loopy_reasoning_medium_cap8192"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("paper/artifacts/tool_comparison_final_audit.json"),
    )
    args = parser.parse_args()

    main_rows = {}
    for method in ("autospec", "sespec", "clause2inv", "naive"):
        rows = _latest(args.non_reasoning_root / "events" / f"{method}.jsonl")
        _assert_coverage(rows, method)
        _assert_reasoning(rows, "none", method)
        main_rows[method] = _summarize(rows)

    r5 = _latest(args.non_reasoning_root / "events" / "loopgym_r5_houdini.jsonl")
    _assert_coverage(r5, "combine@5")
    _assert_reasoning(r5, "none", "combine@5")
    if any(int(row.get("api_call_count") or 0) != 1 for row in r5):
        raise RuntimeError("combine@5: expected exactly one n=5 request per task")
    main_rows["combine5"] = _summarize(r5)

    r10 = [
        row for row in _latest(
            args.non_reasoning_root / "events" / "loopgym_r10_houdini.jsonl"
        )
        if row.get("extension_protocol") == R10_PROTOCOL
    ]
    _assert_coverage(r10, "combine@10")
    _assert_reasoning(r10, "none", "combine@10")
    if any(
        row.get("generation_status") != "completed"
        or int(row.get("api_call_count") or 0) != 2
        or int(row.get("rollout_count") or 0) != 10
        for row in r10
    ):
        raise RuntimeError("combine@10: expected two n=5 calls and ten rollouts")
    if any("verified" not in row for row in r10):
        raise RuntimeError("combine@10: common scoring has not completed")
    main_rows["combine10"] = _summarize(r10)

    loopy_none = [
        row for row in _latest(args.non_reasoning_root / "events" / "loopy.jsonl")
        if row.get("loopy_protocol") == LOOPY_NONE_PROTOCOL
    ]
    _assert_coverage(loopy_none, "Loopy none")
    _assert_reasoning(loopy_none, "none", "Loopy none")
    if any(row.get("generation_status") != "completed" for row in loopy_none):
        raise RuntimeError("Loopy none: not every task completed")
    main_rows["loopy"] = _summarize(loopy_none)

    medium = [
        row for row in _latest(args.medium_root / "events" / "loopy.jsonl")
        if row.get("loopy_protocol") == LOOPY_MEDIUM_PROTOCOL
    ]
    _assert_coverage(medium, "Loopy medium")
    _assert_reasoning(medium, "medium", "Loopy medium")
    if any(row.get("generation_status") != "completed" for row in medium):
        raise RuntimeError("Loopy medium: not every task completed")

    paired = _paired(args.paired_root / "paired_pass1_combine1.jsonl")
    source = _latest(args.paired_root / "events" / "loopgym_r1_houdini.jsonl")
    _assert_coverage(source, "paired source")
    _assert_reasoning(source, "none", "paired source")
    main_rows.update(paired)

    artifact = {
        "schema_version": 1,
        "main_configuration": "non-thinking/reasoning_effort=none",
        "main": main_rows,
        "appendix": {
            "loopy_reasoning_medium": _summarize(medium),
        },
        "protocols": {
            "combine10": R10_PROTOCOL,
            "loopy_none": LOOPY_NONE_PROTOCOL,
            "loopy_medium": LOOPY_MEDIUM_PROTOCOL,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
